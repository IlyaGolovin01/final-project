"""Пользовательские сценарии: FAQ, поиск по тексту и голосу, создание заявок."""

from __future__ import annotations

import logging

import config
import db
import helpers
import session
import stt
import ui
from kb import kb, stems

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Общая логика ответа на свободный вопрос                                    #
# --------------------------------------------------------------------------- #
# Подсказки для сообщений, которые пришли посреди пошагового диалога в
# неподходящем виде (голосовое там, где ждём контакт; стикер вместо текста).
# Без этого такое сообщение уходило бы в поиск по FAQ, а незаконченная
# заявка молча висела бы дальше.
_STEP_HINTS = {
    session.STEP_TICKET_TEXT: "Сейчас жду описание проблемы — напишите его словами.",
    session.STEP_TICKET_CONTACT: (
        "Сейчас жду контакт для связи: телефон, почту или номер заказа. "
        "Можно нажать «⏭ Пропустить» в сообщении выше."
    ),
    session.STEP_TICKET_CONFIRM: (
        "Заявка готова — нажмите «✅ Отправить заявку», «✏️ Переписать» "
        "или «✖️ Отмена» в сообщении выше."
    ),
    session.STEP_TICKET_APPEND: "Напишите текстом, что добавить к заявке.",
    session.STEP_STAFF_REPLY: "Сейчас жду текст ответа клиенту.",
    session.STEP_STAFF_CLOSE: "Сейчас жду описание решения по заявке.",
    session.STEP_STAFF_FIND: "Сейчас жду номер заявки.",
}


def _step_hint_sent(bot, message) -> bool:
    """Отвечает подсказкой, если идёт пошаговый диалог. True — сообщение обработано."""
    hint = _STEP_HINTS.get(session.step(message.from_user.id))
    if not hint:
        return False
    bot.send_message(message.chat.id, f"{hint}\n\n<i>/cancel — отменить.</i>")
    return True


def handle_free_text(bot, message, query: str, source: str = "text") -> None:
    """Ищет ответ в базе знаний и решает, что показать пользователю."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Приветствия и «пустые» фразы: значимых слов нет, искать нечего.
    if not stems(query):
        bot.send_message(
            chat_id,
            "Здравствуйте! Опишите вопрос своими словами — например «где мой заказ» "
            "или «товар пришёл повреждённым». Или выберите тему:",
            reply_markup=ui.categories_kb(),
        )
        return

    matches = kb.search(query)
    best = matches[0] if matches else None

    # 1. Уверенное попадание — отвечаем сразу.
    if best and best.score >= config.FAQ_DIRECT_THRESHOLD:
        log_id = db.log_query(
            user_id=user_id,
            query=query,
            matched_id=best.item.id,
            score=best.score,
            outcome="answered",
            source=source,
        )
        bot.send_message(
            chat_id, ui.answer_text(best.item), reply_markup=ui.answer_kb(best.item, log_id)
        )
        return

    # 2. Есть похожие — предлагаем варианты кнопками.
    candidates = [m for m in matches if m.score >= config.FAQ_SUGGEST_THRESHOLD][
        : config.FAQ_MAX_SUGGESTIONS
    ]
    if candidates:
        db.log_query(
            user_id=user_id,
            query=query,
            matched_id=candidates[0].item.id,
            score=candidates[0].score,
            outcome="suggested",
            source=source,
        )
        dept_code = kb.classify_department(query)
        bot.send_message(
            chat_id,
            ui.suggestions_text(query),
            reply_markup=ui.suggestions_kb(candidates, dept_code),
        )
        return

    # 3. Ничего не нашли — предлагаем специалиста и запоминаем вопрос.
    db.log_query(
        user_id=user_id,
        query=query,
        matched_id=None,
        score=best.score if best else 0.0,
        outcome="not_found",
        source=source,
    )
    dept_code = kb.classify_department(query)
    bot.send_message(
        chat_id,
        ui.not_found_text(query, dept_code),
        reply_markup=ui.not_found_kb(dept_code),
    )


def _accept_ticket_text(bot, message, text: str, source: str) -> None:
    """Принимает описание проблемы и переходит к запросу контакта."""
    user_id = message.from_user.id
    chat_id = message.chat.id

    if len(text) < 5:
        bot.send_message(
            chat_id,
            "Слишком коротко — я не пойму суть проблемы. "
            "Опишите, пожалуйста, чуть подробнее.",
            reply_markup=ui.cancel_kb(),
        )
        return
    if len(text) > config.MAX_TICKET_TEXT:
        text = text[: config.MAX_TICKET_TEXT]
        bot.send_message(
            chat_id,
            f"Текст длинный, оставил первые {config.MAX_TICKET_TEXT} символов — "
            "остальное можно дописать в заявку позже.",
        )

    session.set_step(user_id, session.STEP_TICKET_CONTACT, message=text, source=source)
    bot.send_message(
        chat_id,
        "Как с вами связаться, если понадобится уточнить? "
        "Пришлите телефон, почту или номер заказа.\n\n"
        "<i>Можно пропустить — тогда специалист ответит здесь, в чате.</i>",
        reply_markup=ui.contact_kb(),
    )


def _show_preview(bot, chat_id: int, user_id: int) -> None:
    data = session.data(user_id)
    dept = config.department(data.get("department"))
    contact = data.get("contact") or "—"
    session.set_step(user_id, session.STEP_TICKET_CONFIRM)
    bot.send_message(
        chat_id,
        "<b>Проверьте заявку перед отправкой</b>\n\n"
        f"Отдел: {dept.emoji} {ui.esc(dept.short)}\n"
        f"Контакт: {ui.esc(contact)}\n\n"
        f"<b>Описание</b>\n{ui.esc(data.get('message'))}",
        reply_markup=ui.confirm_kb(),
    )


# --------------------------------------------------------------------------- #
#  Регистрация обработчиков                                                   #
# --------------------------------------------------------------------------- #
def register(bot) -> None:  # noqa: C901 — линейный список обработчиков
    # ---------------------------------------------------------------- команды
    @bot.message_handler(commands=["start"])
    def cmd_start(message):
        session.clear(message.from_user.id)
        db.touch_user(
            message.from_user.id, message.from_user.username, helpers.display_name(message.from_user)
        )
        bot.send_message(
            message.chat.id,
            ui.greeting(message.from_user.first_name),
            reply_markup=ui.main_menu(),
        )
        bot.send_message(
            message.chat.id, ui.categories_text(), reply_markup=ui.categories_kb()
        )

    @bot.message_handler(commands=["help"])
    def cmd_help(message):
        session.clear(message.from_user.id)
        bot.send_message(message.chat.id, ui.HELP_TEXT, reply_markup=ui.main_menu())

    @bot.message_handler(commands=["cancel"])
    def cmd_cancel(message):
        was_busy = session.is_busy(message.from_user.id)
        session.clear(message.from_user.id)
        prefix = "Отменил." if was_busy else "Отменять нечего."
        bot.send_message(
            message.chat.id, f"{prefix} Чем ещё помочь?", reply_markup=ui.main_menu()
        )

    @bot.message_handler(commands=["faq"])
    def cmd_faq(message):
        session.clear(message.from_user.id)
        bot.send_message(
            message.chat.id, ui.categories_text(), reply_markup=ui.categories_kb()
        )

    @bot.message_handler(commands=["about"])
    def cmd_about(message):
        session.clear(message.from_user.id)
        bot.send_message(message.chat.id, ui.about(), reply_markup=ui.main_menu())

    @bot.message_handler(commands=["ticket"])
    def cmd_ticket(message):
        session.clear(message.from_user.id)
        bot.send_message(
            message.chat.id, ui.dept_choice_text(), reply_markup=ui.dept_choice_kb()
        )

    @bot.message_handler(commands=["my"])
    def cmd_my(message):
        session.clear(message.from_user.id)
        tickets = db.list_tickets(user_id=message.from_user.id, limit=10)
        if not tickets:
            bot.send_message(
                message.chat.id,
                "У вас пока нет заявок. Если понадобится помощь специалиста — "
                f"нажмите «{ui.BTN_SPECIALIST}».",
                reply_markup=ui.main_menu(),
            )
            return
        bot.send_message(
            message.chat.id,
            f"Ваши заявки ({len(tickets)}):",
            reply_markup=ui.my_tickets_kb(tickets),
        )

    # ------------------------------------------------- кнопки нижнего меню
    # Регистрируются до пошаговых обработчиков: нажатие кнопки меню
    # всегда прерывает незаконченный диалог.
    @bot.message_handler(func=lambda m: m.text == ui.BTN_FAQ, content_types=["text"])
    def btn_faq(message):
        cmd_faq(message)

    @bot.message_handler(func=lambda m: m.text == ui.BTN_ABOUT, content_types=["text"])
    def btn_about(message):
        cmd_about(message)

    @bot.message_handler(
        func=lambda m: m.text == ui.BTN_SPECIALIST, content_types=["text"]
    )
    def btn_specialist(message):
        cmd_ticket(message)

    @bot.message_handler(
        func=lambda m: m.text == ui.BTN_MY_TICKETS, content_types=["text"]
    )
    def btn_my(message):
        cmd_my(message)

    # ------------------------------------------------------- шаги диалогов
    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_TICKET_TEXT,
        content_types=["voice", "audio"],
    )
    def ticket_text_voice(message):
        media = message.voice or message.audio
        notice = bot.send_message(message.chat.id, "🎧 Слушаю голосовое сообщение…")
        try:
            text = stt.transcribe(bot, media)
        except stt.STTError as exc:
            bot.edit_message_text(
                str(exc), chat_id=message.chat.id, message_id=notice.message_id
            )
            return
        bot.edit_message_text(
            f"🎤 Распознал: «{ui.esc(text)}»",
            chat_id=message.chat.id,
            message_id=notice.message_id,
        )
        _accept_ticket_text(bot, message, text, source="voice")

    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_TICKET_TEXT,
        content_types=["text", "photo", "document"],
    )
    def ticket_text(message):
        text = (message.text or message.caption or "").strip()
        if not text:
            bot.send_message(
                message.chat.id,
                "Вложение вижу, но мне нужно описание проблемы словами. "
                "Напишите, пожалуйста, что случилось — файлы приложите потом "
                "в переписке по заявке.",
                reply_markup=ui.cancel_kb(),
            )
            return
        _accept_ticket_text(bot, message, text, source="text")

    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_TICKET_CONTACT,
        content_types=["text"],
    )
    def ticket_contact(message):
        session.update(message.from_user.id, contact=(message.text or "").strip()[:200])
        _show_preview(bot, message.chat.id, message.from_user.id)

    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_TICKET_CONFIRM,
        content_types=["text"],
    )
    def ticket_confirm_nudge(message):
        bot.send_message(
            message.chat.id,
            "Заявка готова — нажмите «✅ Отправить заявку» выше, "
            "«✏️ Переписать» или «✖️ Отмена».",
            reply_markup=ui.confirm_kb(),
        )

    @bot.message_handler(
        func=lambda m: session.step(m.from_user.id) == session.STEP_TICKET_APPEND,
        content_types=["text"],
    )
    def ticket_append(message):
        user_id = message.from_user.id
        data = session.data(user_id)
        ticket_id = int(data.get("ticket_id") or 0)
        text = (message.text or "").strip()[: config.MAX_TICKET_TEXT]
        session.clear(user_id)

        ticket = db.get_ticket(ticket_id)
        if not ticket or ticket["user_id"] != user_id:
            bot.send_message(
                message.chat.id, "Заявка не найдена.", reply_markup=ui.main_menu()
            )
            return

        db.add_comment(
            ticket_id, user_id, helpers.display_name(message.from_user), text, is_staff=False
        )
        if ticket["status"] == db.STATUS_CLOSED:
            ticket = db.reopen_ticket(ticket_id) or ticket

        bot.send_message(
            message.chat.id,
            f"Добавил к заявке {ui.esc(ticket['ticket_no'])} и передал специалисту.",
            reply_markup=ui.main_menu(),
        )
        helpers.notify_staff(
            bot,
            ticket,
            text=(
                f"💬 <b>Дополнение к заявке {ui.esc(ticket['ticket_no'])}</b>\n"
                f"от {ui.esc(helpers.display_name(message.from_user))}\n\n"
                f"{ui.esc(text)}"
            ),
        )

    # ------------------------------------------------ свободный ввод (текст)
    @bot.message_handler(content_types=["voice", "audio"])
    def free_voice(message):
        # Голосовое посреди диалога (например, там, где ждём контакт):
        # не пускаем его в поиск по FAQ, чтобы не потерять черновик заявки.
        if _step_hint_sent(bot, message):
            return
        db.touch_user(
            message.from_user.id,
            message.from_user.username,
            helpers.display_name(message.from_user),
        )
        media = message.voice or message.audio
        notice = bot.send_message(message.chat.id, "🎧 Слушаю голосовое сообщение…")
        try:
            text = stt.transcribe(bot, media)
        except stt.STTError as exc:
            bot.edit_message_text(
                str(exc), chat_id=message.chat.id, message_id=notice.message_id
            )
            bot.send_message(
                message.chat.id, ui.categories_text(), reply_markup=ui.categories_kb()
            )
            return
        bot.edit_message_text(
            f"🎤 Распознал: «{ui.esc(text)}»\n\nИщу ответ…",
            chat_id=message.chat.id,
            message_id=notice.message_id,
        )
        handle_free_text(bot, message, text, source="voice")

    @bot.message_handler(content_types=["text"])
    def free_text(message):
        db.touch_user(
            message.from_user.id,
            message.from_user.username,
            helpers.display_name(message.from_user),
        )
        query = (message.text or "").strip()
        if len(query) < 3:
            bot.send_message(
                message.chat.id,
                "Напишите, пожалуйста, чуть подробнее — по одному-двум символам "
                "я не пойму вопрос.",
                reply_markup=ui.categories_kb(),
            )
            return
        handle_free_text(bot, message, query, source="text")

    @bot.message_handler(
        content_types=[
            "photo", "document", "sticker", "video", "video_note",
            "animation", "location", "contact", "venue", "dice", "poll",
        ]
    )
    def free_other(message):
        if _step_hint_sent(bot, message):
            return
        voice_note = " и голосовыми сообщениями" if config.stt_enabled() else ""
        bot.send_message(
            message.chat.id,
            f"Я работаю с текстом{voice_note}. Опишите проблему словами — найду ответ.\n\n"
            "Скриншоты и фото пригодятся специалисту: создайте заявку через "
            f"«{ui.esc(ui.BTN_SPECIALIST)}» и приложите их в переписке.",
            reply_markup=ui.categories_kb(),
        )

    # ------------------------------------------------------------- колбэки
    @bot.callback_query_handler(func=lambda c: c.data == "cats")
    def cb_categories(call):
        session.clear(call.from_user.id)
        helpers.safe_edit(bot, call, ui.categories_text(), ui.categories_kb())
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
    def cb_category(call):
        cat_id = call.data.split(":", 1)[1]
        cat = kb.category(cat_id)
        items = kb.items_of(cat_id)
        if not cat or not items:
            bot.answer_callback_query(
                call.id, "Тема не найдена, откройте список заново.", show_alert=True
            )
            return
        helpers.safe_edit(
            bot,
            call,
            f"{cat.emoji} <b>{ui.esc(cat.title)}</b>\n\nВыберите вопрос:",
            ui.category_items_kb(cat_id),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("q:"))
    def cb_question(call):
        item = kb.get(call.data.split(":", 1)[1])
        if not item:
            bot.answer_callback_query(
                call.id, "Вопрос не найден, откройте список заново.", show_alert=True
            )
            return
        # Пользователь вернулся к чтению FAQ — незаконченный черновик заявки
        # больше не актуален, иначе следующее сообщение уйдёт не туда.
        session.clear(call.from_user.id)
        log_id = db.log_query(
            user_id=call.from_user.id,
            query=item.question,
            matched_id=item.id,
            score=1.0,
            outcome="category",
        )
        helpers.safe_edit(bot, call, ui.answer_text(item), ui.answer_kb(item, log_id))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("hlp:"))
    def cb_helpful(call):
        parts = call.data.split(":")
        if len(parts) < 4:
            bot.answer_callback_query(call.id)
            return
        try:
            log_id = int(parts[1])
        except ValueError:
            bot.answer_callback_query(call.id)
            return

        helpful = parts[2] == "1"
        dept_code = parts[3]
        db.mark_helpful(log_id, helpful)

        # В обоих случаях убираем 👍/👎: повторное нажатие ничего не даст,
        # а на «не помогло» ещё и заново взводило бы черновик заявки.
        try:
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=ui.back_to_topics_kb(),
            )
        except Exception:  # noqa: BLE001 — сообщение могли удалить, это не ошибка
            pass

        if helpful:
            session.clear(call.from_user.id)
            bot.answer_callback_query(call.id, "Спасибо за отзыв!")
            return

        # Обещаем передать вопрос специалисту — значит, следующее сообщение
        # должно стать текстом заявки, а не новым запросом к базе знаний.
        dept = config.department(dept_code)
        session.clear(call.from_user.id)
        session.set_step(
            call.from_user.id, session.STEP_TICKET_TEXT, department=dept.code
        )
        bot.answer_callback_query(call.id, "Понял, подключим человека.")
        bot.send_message(
            call.message.chat.id,
            "Жаль, что ответ не подошёл. Опишите ситуацию своими словами — "
            f"передам её в {dept.emoji} <b>{ui.esc(dept.short.lower())}</b>.\n\n"
            "<i>Нужен другой отдел — выберите кнопкой ниже. /cancel — отменить.</i>",
            reply_markup=ui.not_found_kb(dept.code),
        )

    @bot.callback_query_handler(func=lambda c: c.data == "esc")
    def cb_escalate_choose(call):
        session.clear(call.from_user.id)
        helpers.safe_edit(bot, call, ui.dept_choice_text(), ui.dept_choice_kb())
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("esc:"))
    def cb_escalate_start(call):
        dept = config.department(call.data.split(":", 1)[1])
        session.clear(call.from_user.id)
        session.set_step(
            call.from_user.id, session.STEP_TICKET_TEXT, department=dept.code
        )
        voice_hint = " Можно записать голосовое сообщение." if config.stt_enabled() else ""
        helpers.safe_edit(
            bot,
            call,
            f"Заявка в {dept.emoji} <b>{ui.esc(dept.short)}</b>.\n\n"
            f"Опишите, что случилось.{voice_hint}\n\n"
            "<i>Чем конкретнее, тем быстрее решим. Полезно указать: номер заказа, "
            "что именно вы делали, что увидели вместо ожидаемого, текст ошибки.</i>",
            ui.cancel_kb(),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "skip")
    def cb_contact_skip(call):
        if session.step(call.from_user.id) != session.STEP_TICKET_CONTACT:
            bot.answer_callback_query(
                call.id, "Черновик заявки устарел — начните заново.", show_alert=True
            )
            return
        session.update(call.from_user.id, contact=None)
        bot.answer_callback_query(call.id, "Пропустили")
        _show_preview(bot, call.message.chat.id, call.from_user.id)

    @bot.callback_query_handler(func=lambda c: c.data == "redo")
    def cb_ticket_redo(call):
        if session.step(call.from_user.id) != session.STEP_TICKET_CONFIRM:
            bot.answer_callback_query(
                call.id, "Черновик заявки устарел — начните заново.", show_alert=True
            )
            return
        session.set_step(call.from_user.id, session.STEP_TICKET_TEXT)
        helpers.safe_edit(
            bot, call, "Хорошо, опишите проблему заново.", ui.cancel_kb()
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "send")
    def cb_ticket_send(call):
        user = call.from_user
        if session.step(user.id) != session.STEP_TICKET_CONFIRM:
            bot.answer_callback_query(
                call.id, "Черновик заявки устарел — начните заново.", show_alert=True
            )
            return
        data = session.data(user.id)
        session.clear(user.id)

        ticket = db.create_ticket(
            user_id=user.id,
            username=user.username,
            full_name=helpers.display_name(user),
            department=config.department(data.get("department")).code,
            message=str(data.get("message") or ""),
            contact=data.get("contact"),
            source=str(data.get("source") or "text"),
        )
        helpers.safe_edit(bot, call, ui.ticket_created(ticket))
        bot.answer_callback_query(call.id, "Заявка отправлена")
        helpers.notify_staff(bot, ticket)

    @bot.callback_query_handler(func=lambda c: c.data == "cancel")
    def cb_cancel(call):
        session.clear(call.from_user.id)
        helpers.safe_edit(
            bot,
            call,
            "Отменил. Если понадоблюсь — напишите вопрос или откройте темы.",
            ui.categories_kb(),
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == "mytk")
    def cb_my_tickets(call):
        tickets = db.list_tickets(user_id=call.from_user.id, limit=10)
        if not tickets:
            helpers.safe_edit(bot, call, "У вас пока нет заявок.", ui.categories_kb())
            bot.answer_callback_query(call.id)
            return
        helpers.safe_edit(
            bot, call, f"Ваши заявки ({len(tickets)}):", ui.my_tickets_kb(tickets)
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("tk:"))
    def cb_my_ticket(call):
        try:
            ticket_id = int(call.data.split(":", 1)[1])
        except ValueError:
            bot.answer_callback_query(call.id)
            return
        ticket = db.get_ticket(ticket_id)
        if not ticket or ticket["user_id"] != call.from_user.id:
            bot.answer_callback_query(call.id, "Заявка не найдена.", show_alert=True)
            return

        text = ui.ticket_card(ticket)
        comments = db.get_comments(ticket_id)
        if comments:
            text += "\n\n<b>Переписка</b>"
            for comment in comments:
                who = "Поддержка" if comment["is_staff"] else "Вы"
                text += (
                    f"\n\n<i>{who}, {ui.esc(comment['created_at'])}</i>\n"
                    f"{ui.esc(comment['text'])}"
                )
        helpers.safe_edit(bot, call, text, ui.my_ticket_kb(ticket))
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith("tkadd:"))
    def cb_ticket_append(call):
        try:
            ticket_id = int(call.data.split(":", 1)[1])
        except ValueError:
            bot.answer_callback_query(call.id)
            return
        ticket = db.get_ticket(ticket_id)
        if not ticket or ticket["user_id"] != call.from_user.id:
            bot.answer_callback_query(call.id, "Заявка не найдена.", show_alert=True)
            return
        session.set_step(
            call.from_user.id, session.STEP_TICKET_APPEND, ticket_id=ticket_id
        )
        bot.send_message(
            call.message.chat.id,
            f"Что добавить к заявке {ui.esc(ticket['ticket_no'])}? "
            "Напишите сообщение — оно уйдёт специалисту.",
            reply_markup=ui.cancel_kb(),
        )
        bot.answer_callback_query(call.id)
