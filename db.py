"""Слой доступа к SQLite: заявки специалистам, пользователи, аналитика запросов.

Синхронный код: pyTelegramBotAPI обрабатывает апдейты в пуле потоков,
поэтому соединение открыто с check_same_thread=False и все обращения
к базе защищены одним общим замком.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
import threading
from typing import Any, Sequence

import config

log = logging.getLogger(__name__)

STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_CLOSED = "closed"

STATUS_TITLES = {
    STATUS_NEW: "🆕 Новая",
    STATUS_IN_PROGRESS: "⏳ В работе",
    STATUS_CLOSED: "✅ Закрыта",
}
OPEN_STATUSES = (STATUS_NEW, STATUS_IN_PROGRESS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    full_name      TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    messages_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no   TEXT,
    user_id     INTEGER NOT NULL,
    username    TEXT,
    full_name   TEXT,
    department  TEXT NOT NULL,
    message     TEXT NOT NULL,
    contact     TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    source      TEXT NOT NULL DEFAULT 'text',
    assignee_id INTEGER,
    assignee    TEXT,
    resolution  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_dept   ON tickets(department, status);
CREATE INDEX IF NOT EXISTS idx_tickets_user   ON tickets(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_no ON tickets(ticket_no);

CREATE TABLE IF NOT EXISTS ticket_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id  INTEGER,
    author     TEXT,
    is_staff   INTEGER NOT NULL DEFAULT 0,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_ticket ON ticket_comments(ticket_id);

CREATE TABLE IF NOT EXISTS faq_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    query      TEXT NOT NULL,
    matched_id TEXT,
    score      REAL NOT NULL DEFAULT 0,
    outcome    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'text',
    helpful    INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_faqlog_outcome ON faq_log(outcome);
CREATE INDEX IF NOT EXISTS idx_faqlog_matched ON faq_log(matched_id);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _db() -> sqlite3.Connection:
    if _conn is None:  # pragma: no cover
        raise RuntimeError("База данных не инициализирована: вызовите db.connect()")
    return _conn


def connect() -> None:
    """Открывает соединение и создаёт схему, если её ещё нет."""
    global _conn
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(SCHEMA)
        _conn.commit()
    log.info("База данных готова: %s", config.DB_PATH)


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def _query(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with _lock:
        cur = _db().execute(sql, tuple(params))
        rows = cur.fetchall()
        cur.close()
    return [dict(r) for r in rows]


def _query_one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    rows = _query(sql, params)
    return rows[0] if rows else None


def _execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Выполняет запрос с коммитом, возвращает lastrowid."""
    with _lock:
        cur = _db().execute(sql, tuple(params))
        _db().commit()
        row_id = int(cur.lastrowid or 0)
        cur.close()
    return row_id


# --------------------------------------------------------------------------- #
#  Пользователи                                                               #
# --------------------------------------------------------------------------- #
def touch_user(user_id: int, username: str | None, full_name: str | None) -> None:
    """Регистрирует пользователя или обновляет время последней активности."""
    now = _now()
    _execute(
        """
        INSERT INTO users (user_id, username, full_name, first_seen, last_seen, messages_count)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            last_seen = excluded.last_seen,
            messages_count = users.messages_count + 1
        """,
        (user_id, username, full_name, now, now),
    )


# --------------------------------------------------------------------------- #
#  Заявки                                                                     #
# --------------------------------------------------------------------------- #
def make_ticket_no(ticket_id: int, created_at: str) -> str:
    year = created_at[:4] if len(created_at) >= 4 else _dt.date.today().strftime("%Y")
    return f"TS-{year}-{ticket_id:04d}"


def create_ticket(
    *,
    user_id: int,
    username: str | None,
    full_name: str | None,
    department: str,
    message: str,
    contact: str | None = None,
    source: str = "text",
) -> dict[str, Any]:
    """Создаёт заявку и возвращает её целиком — уже с присвоенным номером."""
    now = _now()
    ticket_id = _execute(
        """
        INSERT INTO tickets
            (user_id, username, full_name, department, message,
             contact, status, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, username, full_name, department, message,
            contact, STATUS_NEW, source, now, now,
        ),
    )
    ticket_no = make_ticket_no(ticket_id, now)
    _execute("UPDATE tickets SET ticket_no = ? WHERE id = ?", (ticket_no, ticket_id))
    log.info("Создана заявка %s (отдел %s, пользователь %s)", ticket_no, department, user_id)
    ticket = get_ticket(ticket_id)
    assert ticket is not None
    return ticket


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    return _query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))


def find_ticket(needle: str) -> dict[str, Any] | None:
    """Ищет заявку по номеру TS-2026-0007, по «7» или по чистому id."""
    needle = (needle or "").strip().upper()
    if not needle:
        return None
    found = _query_one("SELECT * FROM tickets WHERE ticket_no = ?", (needle,))
    if found:
        return found

    tail = needle.rsplit("-", 1)[-1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    if not digits:
        digits = "".join(ch for ch in needle if ch.isdigit())
    if not digits:
        return None
    return get_ticket(int(digits))


def _filters(
    departments: Sequence[str] | None,
    statuses: Sequence[str] | None,
    user_id: int | None,
    assignee_id: int | None,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if departments:
        where.append(f"department IN ({','.join('?' * len(departments))})")
        params.extend(departments)
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    if assignee_id is not None:
        where.append("assignee_id = ?")
        params.append(assignee_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def list_tickets(
    *,
    departments: Sequence[str] | None = None,
    statuses: Sequence[str] | None = None,
    user_id: int | None = None,
    assignee_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clause, params = _filters(departments, statuses, user_id, assignee_id)
    sql = (
        "SELECT * FROM tickets"
        + clause
        + """
        ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,
                 id DESC
        LIMIT ? OFFSET ?
        """
    )
    return _query(sql, params + [limit, offset])


def count_tickets(
    *,
    departments: Sequence[str] | None = None,
    statuses: Sequence[str] | None = None,
    user_id: int | None = None,
    assignee_id: int | None = None,
) -> int:
    clause, params = _filters(departments, statuses, user_id, assignee_id)
    row = _query_one("SELECT COUNT(*) AS n FROM tickets" + clause, params)
    return int(row["n"]) if row else 0


def take_ticket(ticket_id: int, staff_id: int, staff_name: str) -> dict[str, Any] | None:
    _execute(
        """
        UPDATE tickets
           SET status = ?, assignee_id = ?, assignee = ?, updated_at = ?
         WHERE id = ?
        """,
        (STATUS_IN_PROGRESS, staff_id, staff_name, _now(), ticket_id),
    )
    return get_ticket(ticket_id)


def close_ticket(
    ticket_id: int, staff_id: int, staff_name: str, resolution: str | None = None
) -> dict[str, Any] | None:
    now = _now()
    _execute(
        """
        UPDATE tickets
           SET status = ?, closed_at = ?, updated_at = ?, resolution = ?,
               assignee_id = COALESCE(assignee_id, ?), assignee = COALESCE(assignee, ?)
         WHERE id = ?
        """,
        (STATUS_CLOSED, now, now, resolution, staff_id, staff_name, ticket_id),
    )
    return get_ticket(ticket_id)


def reopen_ticket(ticket_id: int) -> dict[str, Any] | None:
    _execute(
        "UPDATE tickets SET status = ?, closed_at = NULL, updated_at = ? WHERE id = ?",
        (STATUS_IN_PROGRESS, _now(), ticket_id),
    )
    return get_ticket(ticket_id)


def move_ticket(ticket_id: int, department: str) -> dict[str, Any] | None:
    _execute(
        "UPDATE tickets SET department = ?, updated_at = ? WHERE id = ?",
        (department, _now(), ticket_id),
    )
    return get_ticket(ticket_id)


# --------------------------------------------------------------------------- #
#  Переписка по заявке                                                        #
# --------------------------------------------------------------------------- #
def add_comment(
    ticket_id: int, author_id: int | None, author: str | None, text: str, is_staff: bool
) -> None:
    _execute(
        """
        INSERT INTO ticket_comments (ticket_id, author_id, author, is_staff, text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticket_id, author_id, author, int(is_staff), text, _now()),
    )
    _execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (_now(), ticket_id))


def get_comments(ticket_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return _query(
        "SELECT * FROM ticket_comments WHERE ticket_id = ? ORDER BY id ASC LIMIT ?",
        (ticket_id, limit),
    )


# --------------------------------------------------------------------------- #
#  Аналитика обращений в базу знаний                                          #
# --------------------------------------------------------------------------- #
def log_query(
    *,
    user_id: int | None,
    query: str,
    matched_id: str | None,
    score: float,
    outcome: str,
    source: str = "text",
) -> int:
    """outcome: answered | suggested | not_found | category."""
    return _execute(
        """
        INSERT INTO faq_log (user_id, query, matched_id, score, outcome, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, (query or "")[:500], matched_id, float(score), outcome, source, _now()),
    )


def mark_helpful(log_id: int, helpful: bool) -> None:
    _execute("UPDATE faq_log SET helpful = ? WHERE id = ?", (int(helpful), log_id))


def unanswered_queries(limit: int = 20) -> list[dict[str, Any]]:
    """Вопросы, на которые бот не нашёл ответа. Основа для пополнения FAQ.

    Берём формулировки самих клиентов: «не найдено» плюс те, где ответ был
    отмечен как бесполезный. Нажатия 👎 на вопрос, открытый кнопкой из списка
    тем (outcome='category'), исключаем — там в query лежит текст вопроса из
    самой базы знаний, добавлять его обратно бессмысленно.

    Группируем в Python, а не в SQL: LOWER() в SQLite умеет только латиницу,
    и «Где мой заказ» с «где мой заказ» попали бы в разные группы.
    """
    rows = _query(
        """
        SELECT query, created_at
          FROM faq_log
         WHERE outcome = 'not_found'
            OR (helpful = 0 AND outcome <> 'category')
         ORDER BY id DESC
         LIMIT 2000
        """
    )

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        text = (row["query"] or "").strip()
        if not text:
            continue
        entry = grouped.get(text.lower())
        if entry is None:
            grouped[text.lower()] = {"query": text, "n": 1, "last_at": row["created_at"]}
            continue
        entry["n"] += 1
        if str(row["created_at"]) > str(entry["last_at"]):
            entry["last_at"] = row["created_at"]

    result = sorted(grouped.values(), key=lambda e: e["last_at"], reverse=True)
    result.sort(key=lambda e: e["n"], reverse=True)
    return result[:limit]


def stats() -> dict[str, Any]:
    """Сводка для админ-панели."""
    result: dict[str, Any] = {}

    row = _query_one("SELECT COUNT(*) AS n FROM users")
    result["users"] = int(row["n"]) if row else 0

    result["by_status"] = {
        r["status"]: int(r["n"])
        for r in _query("SELECT status, COUNT(*) AS n FROM tickets GROUP BY status")
    }

    by_dept: dict[str, dict[str, int]] = {}
    for r in _query(
        "SELECT department, status, COUNT(*) AS n FROM tickets GROUP BY department, status"
    ):
        by_dept.setdefault(r["department"], {})[r["status"]] = int(r["n"])
    result["by_dept"] = by_dept

    row = _query_one(
        "SELECT COUNT(*) AS n FROM tickets WHERE DATE(created_at) = DATE('now', 'localtime')"
    )
    result["today"] = int(row["n"]) if row else 0

    row = _query_one("SELECT COUNT(*) AS n FROM faq_log")
    result["queries"] = int(row["n"]) if row else 0

    row = _query_one(
        "SELECT COUNT(*) AS n FROM faq_log WHERE outcome IN ('answered', 'category')"
    )
    result["queries_answered"] = int(row["n"]) if row else 0

    result["top_topics"] = _query(
        """
        SELECT matched_id, COUNT(*) AS n
          FROM faq_log
         WHERE matched_id IS NOT NULL
         GROUP BY matched_id ORDER BY n DESC LIMIT 5
        """
    )

    row = _query_one(
        """
        SELECT AVG(JULIANDAY(closed_at) - JULIANDAY(created_at)) * 24 AS hours
          FROM tickets WHERE status = 'closed' AND closed_at IS NOT NULL
        """
    )
    result["avg_close_hours"] = float(row["hours"]) if row and row["hours"] else None

    return result


def export_rows() -> list[dict[str, Any]]:
    """Все заявки — для выгрузки в CSV."""
    return _query("SELECT * FROM tickets ORDER BY id")
