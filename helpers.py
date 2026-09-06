"""Мелкие общие помощники для обработчиков."""

from __future__ import annotations

import logging
from typing import Any

import config
import ui

log = logging.getLogger(__name__)


def display_name(user: Any) -> str:
    """Читаемое имя пользователя Telegram."""
    if user is None:
        return "—"
    parts = [getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (getattr(user, "username", None) or str(getattr(user, "id", "—")))


def safe_edit(bot, call, text: str, markup=None) -> None:
    """Редактирует сообщение; если не получилось — отправляет новое."""
    try:
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
        )
    except Exception as exc:  # noqa: BLE001 — например «message is not modified»
        log.debug("edit_message_text не удался (%s), отправляю новое сообщение", exc)
        try:
            bot.send_message(call.message.chat.id, text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            log.warning("Не удалось доставить сообщение в чат %s", call.message.chat.id)


def notify_staff(bot, ticket: dict, text: str | None = None) -> None:
    """Рассылает сотрудникам нужного отдела уведомление по заявке."""
    if not config.NOTIFY_STAFF:
        return
    body = text or ui.staff_notification(ticket)
    markup = ui.admin_ticket_kb(ticket)
    for staff_id in config.staff_ids(ticket["department"]):
        try:
            bot.send_message(staff_id, body, reply_markup=markup)
        except Exception as exc:  # noqa: BLE001 — сотрудник мог не открыть чат с ботом
            log.warning("Не удалось уведомить сотрудника %s: %s", staff_id, exc)


def notify_user(bot, user_id: int, text: str, markup=None) -> bool:
    """Пишет клиенту. Возвращает False, если доставить не удалось."""
    try:
        bot.send_message(user_id, text, reply_markup=markup)
        return True
    except Exception as exc:  # noqa: BLE001 — клиент мог заблокировать бота
        log.warning("Не удалось написать пользователю %s: %s", user_id, exc)
        return False
