"""Простейшая машина состояний для пошаговых диалогов.

pyTelegramBotAPI не навязывает FSM, поэтому состояние храним сами:
шаг диалога и накопленные данные для каждого пользователя.
Хранилище в памяти — при перезапуске бота незавершённые диалоги
сбрасываются, готовые заявки при этом остаются в базе.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

# --- шаги диалогов ---------------------------------------------------------- #
# Пользователь создаёт заявку
STEP_TICKET_TEXT = "ticket_text"
STEP_TICKET_CONTACT = "ticket_contact"
STEP_TICKET_CONFIRM = "ticket_confirm"
# Пользователь дописывает сообщение в существующую заявку
STEP_TICKET_APPEND = "ticket_append"
# Сотрудник отвечает клиенту / закрывает заявку / ищет заявку
STEP_STAFF_REPLY = "staff_reply"
STEP_STAFF_CLOSE = "staff_close"
STEP_STAFF_FIND = "staff_find"


@dataclass
class Session:
    step: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


_lock = threading.RLock()
_sessions: dict[int, Session] = {}


def get(user_id: int) -> Session:
    with _lock:
        return _sessions.setdefault(user_id, Session())


def step(user_id: int) -> str | None:
    with _lock:
        session = _sessions.get(user_id)
        return session.step if session else None


def set_step(user_id: int, value: str | None, **data: Any) -> Session:
    with _lock:
        session = _sessions.setdefault(user_id, Session())
        session.step = value
        session.data.update(data)
        return session


def update(user_id: int, **data: Any) -> Session:
    with _lock:
        session = _sessions.setdefault(user_id, Session())
        session.data.update(data)
        return session


def data(user_id: int) -> dict[str, Any]:
    with _lock:
        session = _sessions.get(user_id)
        return dict(session.data) if session else {}


def clear(user_id: int) -> None:
    with _lock:
        _sessions.pop(user_id, None)


def is_busy(user_id: int) -> bool:
    """Идёт ли сейчас пошаговый диалог с этим пользователем."""
    return step(user_id) is not None
