"""Настройки бота технической поддержки «Продаем все на свете».

Все значения читаются из переменных окружения или из файла `.env`,
лежащего рядом с этим файлом. Ничего секретного в коде не хранится.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"


# --------------------------------------------------------------------------- #
#  Загрузка .env                                                              #
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path) -> None:
    """Минимальный парсер .env, чтобы не тянуть лишнюю зависимость.

    Настоящие переменные окружения имеют приоритет над файлом.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _get_str(name: str, default: str = "") -> str:
    """Пустая строка в .env считается «не задано» — берём значение по умолчанию."""
    return os.environ.get(name, "").strip() or default


def _get_int(name: str, default: int) -> int:
    raw = _get_str(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    raw = _get_str(name)
    try:
        return float(raw.replace(",", "."))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get_str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "да"}


def _get_id_list(name: str) -> tuple[int, ...]:
    """Читает список Telegram ID через запятую: `ADMIN_IDS=123,456`."""
    raw = _get_str(name)
    result: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.append(int(chunk))
        except ValueError:
            continue
    return tuple(dict.fromkeys(result))  # без дублей, порядок сохранён


# --------------------------------------------------------------------------- #
#  Основные настройки                                                         #
# --------------------------------------------------------------------------- #
BOT_TOKEN: str = _get_str("BOT_TOKEN")

SHOP_NAME: str = _get_str("SHOP_NAME", "Продаем все на свете")
SHOP_SITE: str = _get_str("SHOP_SITE", "https://prodaem-vse.example")
SUPPORT_PHONE: str = _get_str("SUPPORT_PHONE", "8 800 000-00-00")
SUPPORT_EMAIL: str = _get_str("SUPPORT_EMAIL", "support@prodaem-vse.example")
WORKING_HOURS: str = _get_str("WORKING_HOURS", "ежедневно с 09:00 до 21:00 (МСК)")
SLA_HOURS: int = _get_int("SLA_HOURS", 24)

DB_PATH: Path = Path(_get_str("DB_PATH", str(BASE_DIR / "support.db")))
FAQ_PATH: Path = Path(_get_str("FAQ_PATH", str(DATA_DIR / "faq.json")))

LOG_LEVEL: str = _get_str("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = _get_str("LOG_FILE", "")  # пусто = только в консоль


# --------------------------------------------------------------------------- #
#  Права доступа                                                              #
# --------------------------------------------------------------------------- #
# Суперадминистраторы: видят все заявки обоих отделов и статистику.
ADMIN_IDS: tuple[int, ...] = _get_id_list("ADMIN_IDS")

# Сотрудники отделов. Получают уведомления только по своим заявкам.
IT_STAFF_IDS: tuple[int, ...] = _get_id_list("IT_STAFF_IDS")
SALES_STAFF_IDS: tuple[int, ...] = _get_id_list("SALES_STAFF_IDS")


# --------------------------------------------------------------------------- #
#  Отделы                                                                     #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Department:
    """Отдел, в который может быть направлена заявка."""

    code: str
    title: str        # полное название для пользователя
    short: str        # короткое название для кнопок и списков
    emoji: str
    scope: str        # чем занимается — показывается при выборе отдела

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.short}"


DEPT_IT = "it"
DEPT_SALES = "sales"

DEPARTMENTS: dict[str, Department] = {
    DEPT_IT: Department(
        code=DEPT_IT,
        title="Технический отдел",
        short="Технический отдел",
        emoji="🛠",
        scope=(
            "сайт и приложение не работают, ошибки при оформлении, "
            "не проходит оплата, проблемы со входом в личный кабинет, "
            "не приходят письма и коды подтверждения"
        ),
    ),
    DEPT_SALES: Department(
        code=DEPT_SALES,
        title="Отдел продаж",
        short="Отдел продаж",
        emoji="🛒",
        scope=(
            "вопросы по товарам и наличию, цены и скидки, статус и состав заказа, "
            "доставка, возврат, обмен, гарантия и брак"
        ),
    ),
}

DEFAULT_DEPARTMENT = DEPT_SALES


def department(code: str | None) -> Department:
    """Возвращает отдел по коду, безопасно подставляя отдел по умолчанию."""
    return DEPARTMENTS.get(code or "", DEPARTMENTS[DEFAULT_DEPARTMENT])


def staff_ids(dept_code: str) -> tuple[int, ...]:
    """Кому отправлять уведомление о новой заявке этого отдела."""
    per_dept = {
        DEPT_IT: IT_STAFF_IDS,
        DEPT_SALES: SALES_STAFF_IDS,
    }.get(dept_code, ())
    return tuple(dict.fromkeys(ADMIN_IDS + per_dept))


def all_staff_ids() -> tuple[int, ...]:
    return tuple(dict.fromkeys(ADMIN_IDS + IT_STAFF_IDS + SALES_STAFF_IDS))


def is_admin(user_id: int) -> bool:
    """Суперадминистратор: полный доступ к заявкам обоих отделов."""
    return user_id in ADMIN_IDS


def is_staff(user_id: int) -> bool:
    """Любой сотрудник поддержки: есть доступ к админ-панели."""
    return user_id in all_staff_ids()


def visible_departments(user_id: int) -> tuple[str, ...]:
    """Заявки каких отделов сотрудник имеет право видеть."""
    if is_admin(user_id):
        return tuple(DEPARTMENTS)
    result = []
    if user_id in IT_STAFF_IDS:
        result.append(DEPT_IT)
    if user_id in SALES_STAFF_IDS:
        result.append(DEPT_SALES)
    return tuple(result)


# --------------------------------------------------------------------------- #
#  Поиск по базе знаний                                                       #
# --------------------------------------------------------------------------- #
# Если лучший результат набрал столько или больше — отвечаем сразу.
FAQ_DIRECT_THRESHOLD: float = _get_float("FAQ_DIRECT_THRESHOLD", 0.50)
# Если попал в диапазон [SUGGEST; DIRECT) — предлагаем варианты кнопками.
FAQ_SUGGEST_THRESHOLD: float = _get_float("FAQ_SUGGEST_THRESHOLD", 0.20)
# Сколько вариантов показывать в подсказке.
FAQ_MAX_SUGGESTIONS: int = _get_int("FAQ_MAX_SUGGESTIONS", 3)


# --------------------------------------------------------------------------- #
#  Распознавание голосовых сообщений                                          #
# --------------------------------------------------------------------------- #
# none   — голосовые не распознаются, бот вежливо просит написать текстом
# openai — Whisper API (нужен OPENAI_API_KEY)
STT_PROVIDER: str = _get_str("STT_PROVIDER", "none").lower()
OPENAI_API_KEY: str = _get_str("OPENAI_API_KEY")
OPENAI_BASE_URL: str = _get_str("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
STT_MODEL: str = _get_str("STT_MODEL", "whisper-1")
# Пустое значение осмысленно: язык определит сам сервис распознавания,
# поэтому default подставляется только когда параметра нет вовсе.
STT_LANGUAGE: str = os.environ.get("STT_LANGUAGE", "ru").strip()
STT_TIMEOUT: int = _get_int("STT_TIMEOUT", 60)
# Ограничение на длительность голосового, секунды (Telegram отдаёт duration).
STT_MAX_DURATION: int = _get_int("STT_MAX_DURATION", 120)


def stt_enabled() -> bool:
    if STT_PROVIDER == "openai":
        return bool(OPENAI_API_KEY)
    return False


# --------------------------------------------------------------------------- #
#  Прочее                                                                     #
# --------------------------------------------------------------------------- #
# Уведомлять сотрудников о новых заявках в личку.
NOTIFY_STAFF: bool = _get_bool("NOTIFY_STAFF", True)
# Максимальная длина описания проблемы в заявке.
MAX_TICKET_TEXT: int = _get_int("MAX_TICKET_TEXT", 2000)
# Сколько заявок показывать на одной странице в админ-панели.
ADMIN_PAGE_SIZE: int = _get_int("ADMIN_PAGE_SIZE", 5)


class ConfigError(RuntimeError):
    """Некорректная конфигурация — запускать бота нельзя."""


def validate() -> list[str]:
    """Проверяет настройки. Возвращает список предупреждений.

    Критичные ошибки поднимают ConfigError.
    """
    if not BOT_TOKEN:
        raise ConfigError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и укажите токен, "
            "полученный у @BotFather."
        )
    if ":" not in BOT_TOKEN:
        raise ConfigError(
            "BOT_TOKEN выглядит некорректно — ожидается формат "
            "«123456789:AA...». Проверьте значение в .env."
        )
    if not FAQ_PATH.is_file():
        raise ConfigError(f"Не найден файл базы знаний: {FAQ_PATH}")

    warnings: list[str] = []
    if not all_staff_ids():
        warnings.append(
            "Не задан ни один ADMIN_IDS/IT_STAFF_IDS/SALES_STAFF_IDS — "
            "админ-панель будет недоступна, уведомления о заявках никто не получит."
        )
    if STT_PROVIDER == "openai" and not OPENAI_API_KEY:
        warnings.append(
            "STT_PROVIDER=openai, но OPENAI_API_KEY пуст — "
            "распознавание голосовых сообщений отключено."
        )
    if STT_PROVIDER not in {"none", "openai"}:
        warnings.append(
            f"Неизвестный STT_PROVIDER={STT_PROVIDER!r}; поддерживаются 'none' и 'openai'. "
            "Распознавание голосовых отключено."
        )
    if not (0 < FAQ_SUGGEST_THRESHOLD < FAQ_DIRECT_THRESHOLD <= 1):
        warnings.append(
            "Пороги поиска заданы странно: ожидается "
            "0 < FAQ_SUGGEST_THRESHOLD < FAQ_DIRECT_THRESHOLD <= 1."
        )
    return warnings
