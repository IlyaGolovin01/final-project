"""Админ-панель: работа сотрудников отделов с заявками.

Регистрируется ДО пользовательских обработчиков, чтобы пошаговые диалоги
сотрудников не перехватывались общим обработчиком текста.
"""

from __future__ import annotations

import csv
import io
import logging

import config
import db
import helpers
import session
import ui
from kb import kb

log = logging.getLogger(__name__)


def _can_see(user_id: int, ticket: dict) -> bool:
    return ticket.get("department") in config.visible_departments(user_id)


def _ticket_or_none(bot, call, ticket_id: int) -> dict | None:
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        bot.answer_callback_query(call.id, "Заявка не найдена.", show_alert=True)
        return None
    if not _can_see(call.from_user.id, ticket):
        bot.answer_callback_query(
            call.id, "Эта заявка относится к другому отделу.", show_alert=True
        )
        return None
    return ticket


def _show_ticket(bot, call, ticket: dict) -> None:
    text = ui.ticket_card(ticket, for_staff=True)
    comments = db.get_comments(ticket["id"])
    if comments:
        text += "\n\n<b>Переписка</b>"
        for comment in comments:
            who = comment["author"] or ("Поддержка" if comment["is_staff"] else "Клиент")
            tag = "🧑‍💼" if comment["is_staff"] else "🙋"
            text += (
                f"\n\n<i>{tag} {ui.esc(who)}, {ui.esc(comment['created_at'])}</i>\n"
                f"{ui.esc(comment['text'])}"
            )
    helpers.safe_edit(bot, call, text, ui.admin_ticket_kb(ticket))


def _plain_text(message) -> bool:
    """Обычный текст, а не команда и не кнопка меню.

    Обработчики сотрудников регистрируются раньше пользовательских, поэтому
    без этой проверки шаг диалога «съел» бы, например, /cancel.
    """
    text = (message.text or "").strip()
    return bool(text) and not text.startswith("/") and text not in ui.MENU_BUTTONS


# --------------------------------------------------------------------------- #
#  Регистрация                                                                #
# --------------------------------------------------------------------------- #
def register(bot) -> None:  # noqa: C901 — линейный список обработчиков
    # ------------------------------------------------------------- команды
    @bot.message_handler(commands=["admin"])
    def cmd_admin(message):
        user_id = message.from_user.id
        session.clear(user_id)
        if not config.is_staff(user_id):
            bot.send_message(
                message.chat.id,
                "Панель поддержки доступна только сотрудникам.\n\n"
                f"Ваш Telegram ID: <code>{user_id}</code> — передайте его "
                "администратору бота, если доступ нужен.",
                reply_markup=ui.main_menu(),
            )
            return
        bot.send_message(
            message.chat.id,
            ui.admin_menu_text(user_id),
            reply_markup=ui.admin_menu_kb(user_id),
        )

    @bot.message_handler(commands=["id"])
    def cmd_id(message):
        bot.send_message(
            message.chat.id,
            f"Ваш Telegram ID: <code>{message.from_user.id}</code>",
        )

    @bot.message_handler(commands=["export"])
    def cmd_export(message):
        if not config.is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id, "Выгрузка доступна только суперадминистраторам."
            )
            return
        rows = db.export_rows()
        if not rows:
            bot.send_message(message.chat.id, "Заявок пока нет — выгружать нечего.")
            return

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
        payload = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
        payload.name = "tickets.csv"
        bot.send_document(
            message.chat.id,
            payload,
            visible_file_name="tickets.csv",
            caption=f"Выгрузка заявок: {len(rows)} шт.",
        )

    @bot.message_handler(commands=["reload"])
    def cmd_reload(message):
        if not config.is_admin(message.from_user.id):
            bot.send_message(
                message.chat.id, "Перезагрузка базы знаний доступна суперадминистраторам."
            )
            return
        try:
            kb.load()
        except Exception as exc:  # noqa: BLE001 — важно показать причину администратору
            bot.send_message(
                message.chat.id,
                f"❌ Не удалось перечитать базу знаний:\n<code>{ui.esc(exc)}</code>\n\n"
                "Старая версия продолжает работать.",
            )
            return
        bot.send_message(
            message.chat.id,
            f"✅ База знаний перечитана: {len(kb.categories)} категорий, "
            f"{len(kb.items)} вопросов.",
        )

    # ------------------------------------------------- пошаговые диалоги
    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_STAFF_FIND
        and _plain_text(m),
        content_types=["text"],
    )
    def staff_find(message):
        session.clear(message.from_user.id)
        ticket = db.find_ticket(message.text or "")
        if not ticket:
            bot.send_message(
                message.chat.id,
                "Не нашёл такую заявку. Проверьте номер — он выглядит как "
                "<code>TS-2026-0007</code>.",
                reply_markup=ui.admin_back_kb(),
            )
            return
        if not _can_see(message.from_user.id, ticket):
            bot.send_message(
                message.chat.id,
                "Эта заявка относится к другому отделу.",
                reply_markup=ui.admin_back_kb(),
            )
            return
        bot.send_message(
            message.chat.id,
            ui.ticket_card(ticket, for_staff=True),
            reply_markup=ui.admin_ticket_kb(ticket),
        )

    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_STAFF_REPLY
        and _plain_text(m),
        content_types=["text"],
    )
    def staff_reply(message):
        user_id = message.from_user.id
        data = session.data(user_id)
        session.clear(user_id)
        ticket = db.get_ticket(int(data.get("ticket_id") or 0))
        if not ticket:
            bot.send_message(message.chat.id, "Заявка не найдена.")
            return

        text = (message.text or "").strip()[: config.MAX_TICKET_TEXT]
        staff_name = helpers.display_name(message.from_user)
        db.add_comment(ticket["id"], user_id, staff_name, text, is_staff=True)
        if ticket["status"] == db.STATUS_NEW:
            ticket = db.take_ticket(ticket["id"], user_id, staff_name) or ticket

        delivered = helpers.notify_user(
            bot,
            ticket["user_id"],
            f"✉️ <b>Ответ по заявке {ui.esc(ticket['ticket_no'])}</b>\n\n"
            f"{ui.esc(text)}\n\n"
            "<i>Можно ответить прямо здесь — я передам сообщение специалисту "
            "через «📋 Мои заявки».</i>",
        )
        status_line = (
            "Ответ отправлен клиенту."
            if delivered
            else "⚠️ Клиенту доставить не удалось (возможно, он заблокировал бота). "
            "Ответ сохранён в переписке."
        )
        bot.send_message(
            message.chat.id,
            f"{status_line}\n\n" + ui.ticket_card(ticket, for_staff=True),
            reply_markup=ui.admin_ticket_kb(ticket),
        )

    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_STAFF_CLOSE
        and _plain_text(m),
        content_types=["text"],
    )
    def staff_close(message):
        user_id = message.from_user.id
        data = session.data(user_id)
        session.clear(user_id)
        ticket_id = int(data.get("ticket_id") or 0)
        resolution = (message.text or "").strip()[: config.MAX_TICKET_TEXT]
        staff_name = helpers.display_name(message.from_user)

        ticket = db.close_ticket(ticket_id, user_id, staff_name, resolution)
        if not ticket:
            bot.send_message(message.chat.id, "Заявка не найдена.")
            return

        helpers.notify_user(
            bot,
            ticket["user_id"],
            f"✅ <b>Заявка {ui.esc(ticket['ticket_no'])} закрыта</b>\n\n"
            f"{ui.esc(resolution)}\n\n"
            "<i>Если вопрос всё ещё открыт — напишите нам, и мы вернёмся к заявке.</i>",
        )
        bot.send_message(
            message.chat.id,
            "Заявка закрыта, клиент уведомлён.\n\n"
            + ui.ticket_card(ticket, for_staff=True),
            reply_markup=ui.admin_ticket_kb(ticket),
        )

    # ------------------------------------------------------------- колбэки
    @bot.callback_query_handler(func=lambda c: c.data == "a:menu")
    def cb_menu(call):
        user_id = call.from_user.id
        session.clear(user_id)
        if not config.is_staff(user_id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        helpers.safe_edit(
            bot, call, ui.admin_menu_text(user_id), ui.admin_menu_kb(user_id)
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:list:"))
    def cb_list(call):
        user_id = call.from_user.id
        if not config.is_staff(user_id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return

        parts = call.data.split(":", 3)
        if len(parts) < 4:
            bot.answer_callback_query(call.id)
            return
        scope, raw_page = parts[2], parts[3]
        try:
            page = max(0, int(raw_page))
        except ValueError:
            page = 0
        size = config.ADMIN_PAGE_SIZE
        visible = config.visible_departments(user_id)

        if scope == "mine":
            filters = {"assignee_id": user_id, "statuses": db.OPEN_STATUSES}
            title = "🙋 <b>Мои заявки в работе</b>"
        else:
            if scope not in visible:
                bot.answer_callback_query(
                    call.id, "Этот отдел вам недоступен.", show_alert=True
                )
                return
            filters = {"departments": (scope,), "statuses": db.OPEN_STATUSES}
            dept = config.department(scope)
            title = f"{dept.emoji} <b>Открытые заявки — {ui.esc(dept.short)}</b>"

        total = db.count_tickets(**filters)
        tickets = db.list_tickets(**filters, limit=size, offset=page * size)
        if not tickets:
            helpers.safe_edit(
                bot, call, f"{title}\n\nОткрытых заявок нет. Чисто.", ui.admin_back_kb()
            )
            bot.answer_callback_query(call.id)
            return

        shown_from = page * size + 1
        shown_to = page * size + len(tickets)
        helpers.safe_edit(
            bot,
            call,
            f"{title}\n\nВсего: {total}. Показаны {shown_from}–{shown_to}.",
            ui.admin_list_kb(tickets, scope, page, total),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:t:"))
    def cb_ticket(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        try:
            ticket_id = int(call.data.split(":")[2])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id)
            return
        ticket = _ticket_or_none(bot, call, ticket_id)
        if ticket:
            _show_ticket(bot, call, ticket)
            bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:take:"))
    def cb_take(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        try:
            ticket_id = int(call.data.split(":")[2])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id)
            return
        ticket = _ticket_or_none(bot, call, ticket_id)
        if not ticket:
            return
        staff_name = helpers.display_name(call.from_user)
        ticket = db.take_ticket(ticket_id, call.from_user.id, staff_name) or ticket
        helpers.notify_user(
            bot,
            ticket["user_id"],
            f"⏳ Заявка <b>{ui.esc(ticket['ticket_no'])}</b> взята в работу — "
            "специалист уже разбирается.",
        )
        _show_ticket(bot, call, ticket)
        bot.answer_callback_query(call.id, "Взяли в работу")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:reply:"))
    def cb_reply(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        try:
            ticket_id = int(call.data.split(":")[2])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id)
            return
        ticket = _ticket_or_none(bot, call, ticket_id)
        if not ticket:
            return
        session.set_step(
            call.from_user.id, session.STEP_STAFF_REPLY, ticket_id=ticket_id
        )
        bot.send_message(
            call.message.chat.id,
            f"Напишите ответ клиенту по заявке <b>{ui.esc(ticket['ticket_no'])}</b>. "
            "Сообщение уйдёт ему в чат.\n\n/cancel — отменить.",
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:close:"))
    def cb_close(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        try:
            ticket_id = int(call.data.split(":")[2])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id)
            return
        ticket = _ticket_or_none(bot, call, ticket_id)
        if not ticket:
            return
        session.set_step(
            call.from_user.id, session.STEP_STAFF_CLOSE, ticket_id=ticket_id
        )
        bot.send_message(
            call.message.chat.id,
            f"Опишите решение по заявке <b>{ui.esc(ticket['ticket_no'])}</b> — "
            "текст увидит клиент вместе с уведомлением о закрытии.\n\n/cancel — отменить.",
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:reopen:"))
    def cb_reopen(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        try:
            ticket_id = int(call.data.split(":")[2])
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id)
            return
        ticket = _ticket_or_none(bot, call, ticket_id)
        if not ticket:
            return
        ticket = db.reopen_ticket(ticket_id) or ticket
        _show_ticket(bot, call, ticket)
        bot.answer_callback_query(call.id, "Заявка снова в работе")

    @bot.callback_query_handler(func=lambda c: c.data.startswith("a:mv:"))
    def cb_move(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        parts = call.data.split(":")
        if len(parts) < 4:
            bot.answer_callback_query(call.id)
            return
        try:
            ticket_id = int(parts[2])
        except ValueError:
            bot.answer_callback_query(call.id)
            return
        ticket = _ticket_or_none(bot, call, ticket_id)
        if not ticket:
            return

        target = config.department(parts[3])
        ticket = db.move_ticket(ticket_id, target.code) or ticket
        db.add_comment(
            ticket_id,
            call.from_user.id,
            helpers.display_name(call.from_user),
            f"Заявка передана в {target.short}.",
            is_staff=True,
        )
        helpers.notify_staff(bot, ticket)
        _show_ticket(bot, call, ticket)
        bot.answer_callback_query(call.id, f"Передано: {target.short}")

    @bot.callback_query_handler(func=lambda c: c.data == "a:stats")
    def cb_stats(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        helpers.safe_edit(bot, call, ui.stats_text(db.stats()), ui.admin_back_kb())
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "a:unans")
    def cb_unanswered(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        rows = db.unanswered_queries(limit=20)
        helpers.safe_edit(bot, call, ui.unanswered_text(rows), ui.admin_back_kb())
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "a:find")
    def cb_find(call):
        if not config.is_staff(call.from_user.id):
            bot.answer_callback_query(call.id, "Доступ только для сотрудников.", show_alert=True)
            return
        session.set_step(call.from_user.id, session.STEP_STAFF_FIND)
        bot.send_message(
            call.message.chat.id,
            "Пришлите номер заявки — например <code>TS-2026-0007</code> или просто <code>7</code>.\n\n"
            "/cancel — отменить.",
        )
        bot.answer_callback_query(call.id)
