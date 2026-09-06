"""Интерфейс: тексты сообщений и клавиатуры (pyTelegramBotAPI).

Весь текст, который видит пользователь, собран здесь — чтобы его можно было
править, не задевая логику.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Sequence

from telebot import types

import config
import db
from kb import Item, Match, kb

# --------------------------------------------------------------------------- #
#  Кнопки нижнего меню                                                        #
# --------------------------------------------------------------------------- #
BTN_FAQ = "❓ Частые вопросы"
BTN_SPECIALIST = "🧑‍💼 Связаться со специалистом"
BTN_MY_TICKETS = "📋 Мои заявки"
BTN_ABOUT = "ℹ️ Контакты и режим работы"

MENU_BUTTONS = (BTN_FAQ, BTN_SPECIALIST, BTN_MY_TICKETS, BTN_ABOUT)


def esc(text: Any) -> str:
    """Экранирует текст для parse_mode=HTML."""
    return escape(str(text if text is not None else ""), quote=False)


def main_menu() -> types.ReplyKeyboardMarkup:
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        input_field_placeholder="Опишите проблему своими словами…",
    )
    markup.row(types.KeyboardButton(BTN_FAQ))
    markup.row(types.KeyboardButton(BTN_SPECIALIST))
    markup.row(types.KeyboardButton(BTN_MY_TICKETS), types.KeyboardButton(BTN_ABOUT))
    return markup


def _inline(*rows: Sequence[types.InlineKeyboardButton]) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        if row:
            markup.row(*row)
    return markup


def btn(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


# --------------------------------------------------------------------------- #
#  Тексты                                                                     #
# --------------------------------------------------------------------------- #
def greeting(name: str | None) -> str:
    who = f", {esc(name)}" if name else ""
    voice_line = (
        "🎤 Можно записать <b>голосовое сообщение</b> — я его расшифрую.\n"
        if config.stt_enabled()
        else ""
    )
    return (
        f"Здравствуйте{who}! Я бот поддержки «{esc(config.SHOP_NAME)}».\n\n"
        "Чем могу помочь:\n"
        "• <b>Опишите проблему своими словами</b> — найду готовый ответ.\n"
        f"• Или выберите тему в разделе «{esc(BTN_FAQ)}».\n"
        f"{voice_line}"
        "• Если ответа не хватит — передам вопрос живому специалисту "
        "и пришлю номер заявки.\n\n"
        f"Специалисты работают {esc(config.WORKING_HOURS)}."
    )


def about() -> str:
    lines = [
        f"<b>«{esc(config.SHOP_NAME)}» — служба поддержки</b>",
        "",
        f"🕘 Режим работы: {esc(config.WORKING_HOURS)}",
        f"☎️ Телефон: {esc(config.SUPPORT_PHONE)}",
        f"✉️ Почта: {esc(config.SUPPORT_EMAIL)}",
        f"🌐 Сайт: {esc(config.SHOP_SITE)}",
        "",
        "<b>Кто чем занимается</b>",
    ]
    for dept in config.DEPARTMENTS.values():
        lines.append(f"{dept.emoji} <b>{esc(dept.short)}</b> — {esc(dept.scope)}.")
    lines += [
        "",
        f"Первый ответ по заявке — в течение {config.SLA_HOURS} ч. "
        "Заявка из бота обрабатывается быстрее звонка: она сразу попадает "
        "в нужный отдел и не теряется.",
    ]
    return "\n".join(lines)


HELP_TEXT = (
    "<b>Как пользоваться ботом</b>\n\n"
    "• Просто напишите вопрос: «где мой заказ», «как оформить заказ», "
    "«товар пришёл повреждённым».\n"
    f"• «{esc(BTN_FAQ)}» — все темы списком.\n"
    f"• «{esc(BTN_SPECIALIST)}» — создать заявку живому сотруднику.\n"
    f"• «{esc(BTN_MY_TICKETS)}» — статус ваших заявок и переписка по ним.\n\n"
    "<b>Команды</b>\n"
    "/start — в начало\n"
    "/faq — темы вопросов\n"
    "/ticket — создать заявку\n"
    "/my — мои заявки\n"
    "/about — контакты и режим работы\n"
    "/help — эта справка\n"
    "/cancel — отменить текущее действие\n"
    "/id — показать свой Telegram ID"
)


def answer_text(item: Item) -> str:
    dept = config.department(item.dept)
    body = kb.render(item.answer)
    return (
        f"❓ <b>{esc(item.question)}</b>\n\n"
        f"{esc(body)}\n\n"
        f"<i>Если вопрос остался — передам его в {esc(dept.short.lower())}.</i>"
    )


def categories_text() -> str:
    return (
        "Выберите тему — покажу вопросы по ней.\n\n"
        "<i>Подсказка: можно ничего не искать, а просто написать вопрос своими словами.</i>"
    )


def suggestions_text(query: str) -> str:
    return (
        f"Точного ответа на «{esc(query[:80])}» я не нашёл, но, возможно, "
        "вы имели в виду один из этих вопросов:"
    )


def not_found_text(query: str, dept_code: str) -> str:
    dept = config.department(dept_code)
    return (
        f"Не нашёл готового ответа на «{esc(query[:80])}» — такой вопрос лучше "
        "разобрать вручную.\n\n"
        f"Судя по описанию, помочь сможет <b>{esc(dept.short)}</b> "
        f"({esc(dept.scope)}).\n\n"
        "Создать заявку? Опишете ситуацию — специалист ответит "
        f"в течение {config.SLA_HOURS} ч."
    )


# --------------------------------------------------------------------------- #
#  Карточка заявки                                                            #
# --------------------------------------------------------------------------- #
def ticket_card(ticket: dict[str, Any], *, for_staff: bool = False) -> str:
    dept = config.department(ticket.get("department"))
    status = db.STATUS_TITLES.get(ticket.get("status", ""), ticket.get("status", ""))
    lines = [
        f"<b>Заявка {esc(ticket.get('ticket_no'))}</b>",
        f"Статус: {esc(status)}",
        f"Отдел: {dept.emoji} {esc(dept.short)}",
        f"Создана: {esc(ticket.get('created_at'))}",
    ]
    if ticket.get("assignee"):
        lines.append(f"Исполнитель: {esc(ticket['assignee'])}")
    if for_staff:
        author = ticket.get("full_name") or "без имени"
        username = f" (@{esc(ticket['username'])})" if ticket.get("username") else ""
        lines.append(
            f"Клиент: {esc(author)}{username}, id <code>{ticket.get('user_id')}</code>"
        )
        if ticket.get("contact"):
            lines.append(f"Контакт: {esc(ticket['contact'])}")
        if ticket.get("source") == "voice":
            lines.append("Источник: 🎤 голосовое сообщение")
    lines += ["", "<b>Обращение</b>", esc(ticket.get("message"))]
    if ticket.get("resolution"):
        lines += ["", "<b>Решение</b>", esc(ticket["resolution"])]
    return "\n".join(lines)


def ticket_created(ticket: dict[str, Any]) -> str:
    dept = config.department(ticket.get("department"))
    return (
        f"✅ Заявка <b>{esc(ticket.get('ticket_no'))}</b> создана и передана "
        f"в {dept.emoji} <b>{esc(dept.short)}</b>.\n\n"
        f"Первый ответ — в течение {config.SLA_HOURS} ч ({esc(config.WORKING_HOURS)}).\n"
        f"Ответ придёт сюда, в чат. Статус всегда можно посмотреть "
        f"в «{esc(BTN_MY_TICKETS)}».\n\n"
        "Спасибо, что написали — так мы быстрее наведём порядок."
    )


def staff_notification(ticket: dict[str, Any]) -> str:
    return "🔔 <b>Новая заявка</b>\n\n" + ticket_card(ticket, for_staff=True)


# --------------------------------------------------------------------------- #
#  Клавиатуры: пользователь                                                   #
# --------------------------------------------------------------------------- #
def categories_kb() -> types.InlineKeyboardMarkup:
    rows = [[btn(cat.label, f"cat:{cat.id}")] for cat in kb.ordered_categories()]
    rows.append([btn("🧑‍💼 Связаться со специалистом", "esc")])
    return _inline(*rows)


def category_items_kb(cat_id: str) -> types.InlineKeyboardMarkup:
    rows = [[btn(item.short_question, f"q:{item.id}")] for item in kb.items_of(cat_id)]
    cat = kb.category(cat_id)
    dept_code = cat.dept if cat else config.DEFAULT_DEPARTMENT
    rows.append([btn("⬅️ Все темы", "cats"), btn("🧑‍💼 Специалисту", f"esc:{dept_code}")])
    return _inline(*rows)


def answer_kb(item: Item, log_id: int | None) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    if log_id:
        rows.append(
            [
                btn("👍 Помогло", f"hlp:{log_id}:1:{item.dept}"),
                btn("👎 Не помогло", f"hlp:{log_id}:0:{item.dept}"),
            ]
        )
    rows.append([btn("⬅️ Все темы", "cats"), btn("🧑‍💼 Специалисту", f"esc:{item.dept}")])
    return _inline(*rows)


def back_to_topics_kb() -> types.InlineKeyboardMarkup:
    return _inline([btn("⬅️ Все темы", "cats")])


def suggestions_kb(
    matches: Sequence[Match], dept_code: str
) -> types.InlineKeyboardMarkup:
    rows = [[btn(m.item.short_question, f"q:{m.item.id}")] for m in matches]
    rows.append([btn("🧑‍💼 Ни то — нужен специалист", f"esc:{dept_code}")])
    rows.append([btn("⬅️ Все темы", "cats")])
    return _inline(*rows)


def not_found_kb(dept_code: str) -> types.InlineKeyboardMarkup:
    dept = config.department(dept_code)
    rows = [[btn(f"✍️ Создать заявку — {dept.short}", f"esc:{dept.code}")]]
    for code in config.DEPARTMENTS:
        if code != dept.code:
            alt = config.department(code)
            rows.append([btn(f"{alt.emoji} Нет, это в {alt.short.lower()}", f"esc:{code}")])
    rows.append([btn("⬅️ Все темы", "cats")])
    return _inline(*rows)


def dept_choice_kb() -> types.InlineKeyboardMarkup:
    rows = [[btn(d.label, f"esc:{d.code}")] for d in config.DEPARTMENTS.values()]
    rows.append([btn("✖️ Отмена", "cancel")])
    return _inline(*rows)


def dept_choice_text() -> str:
    lines = ["Кому передать вопрос?", ""]
    for dept in config.DEPARTMENTS.values():
        lines.append(f"{dept.emoji} <b>{esc(dept.short)}</b> — {esc(dept.scope)}.")
    lines += ["", "<i>Если не уверены — выбирайте любой, мы перенаправим сами.</i>"]
    return "\n".join(lines)


def cancel_kb() -> types.InlineKeyboardMarkup:
    return _inline([btn("✖️ Отмена", "cancel")])


def contact_kb() -> types.InlineKeyboardMarkup:
    return _inline([btn("⏭ Пропустить", "skip")], [btn("✖️ Отмена", "cancel")])


def confirm_kb() -> types.InlineKeyboardMarkup:
    return _inline(
        [btn("✅ Отправить заявку", "send")],
        [btn("✏️ Переписать", "redo")],
        [btn("✖️ Отмена", "cancel")],
    )


def my_tickets_kb(tickets: Iterable[dict[str, Any]]) -> types.InlineKeyboardMarkup:
    rows = []
    for ticket in tickets:
        status = db.STATUS_TITLES.get(ticket.get("status", ""), "")
        rows.append([btn(f"{status} {ticket.get('ticket_no')}", f"tk:{ticket['id']}")])
    rows.append([btn("⬅️ Все темы", "cats")])
    return _inline(*rows)


def my_ticket_kb(ticket: dict[str, Any]) -> types.InlineKeyboardMarkup:
    return _inline(
        [btn("💬 Дополнить заявку", f"tkadd:{ticket['id']}")],
        [btn("⬅️ К моим заявкам", "mytk")],
    )


# --------------------------------------------------------------------------- #
#  Клавиатуры: сотрудники                                                     #
# --------------------------------------------------------------------------- #
def admin_menu_kb(user_id: int) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    for code in config.visible_departments(user_id):
        dept = config.department(code)
        rows.append([btn(f"{dept.emoji} Открытые — {dept.short}", f"a:list:{code}:0")])
    rows += [
        [btn("🙋 Мои заявки в работе", "a:list:mine:0")],
        [btn("📊 Статистика", "a:stats")],
        [btn("🕳 Вопросы без ответа", "a:unans")],
        [btn("🔎 Найти заявку по номеру", "a:find")],
    ]
    return _inline(*rows)


def admin_menu_text(user_id: int) -> str:
    scopes = ", ".join(
        config.department(code).short.lower()
        for code in config.visible_departments(user_id)
    )
    role = "Суперадминистратор" if config.is_admin(user_id) else "Сотрудник поддержки"
    return (
        "🛠 <b>Панель поддержки</b>\n\n"
        f"Роль: {esc(role)}\n"
        f"Доступные отделы: {esc(scopes or '—')}\n\n"
        "Выберите действие:"
    )


def admin_list_kb(
    tickets: Sequence[dict[str, Any]], scope: str, page: int, total: int
) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = []
    for ticket in tickets:
        mark = "🆕" if ticket.get("status") == db.STATUS_NEW else "⏳"
        preview = (ticket.get("message") or "")[:26].replace("\n", " ")
        rows.append([btn(f"{mark} {ticket.get('ticket_no')} · {preview}", f"a:t:{ticket['id']}")])

    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(btn("◀️ Назад", f"a:list:{scope}:{page - 1}"))
    if (page + 1) * config.ADMIN_PAGE_SIZE < total:
        nav.append(btn("Вперёд ▶️", f"a:list:{scope}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("⬅️ Меню", "a:menu")])
    return _inline(*rows)


def admin_ticket_kb(ticket: dict[str, Any]) -> types.InlineKeyboardMarkup:
    tid = ticket["id"]
    status = ticket.get("status")
    rows: list[list[types.InlineKeyboardButton]] = []
    if status == db.STATUS_NEW:
        rows.append([btn("🙋 Взять в работу", f"a:take:{tid}")])
    if status != db.STATUS_CLOSED:
        rows.append([btn("✉️ Ответить клиенту", f"a:reply:{tid}")])
        rows.append([btn("✅ Закрыть заявку", f"a:close:{tid}")])
        for code in config.DEPARTMENTS:
            if code != ticket.get("department"):
                alt = config.department(code)
                rows.append([btn(f"↪️ Передать в {alt.short.lower()}", f"a:mv:{tid}:{code}")])
    else:
        rows.append([btn("↩️ Переоткрыть", f"a:reopen:{tid}")])
    rows.append([btn("⬅️ Меню", "a:menu")])
    return _inline(*rows)


def admin_back_kb() -> types.InlineKeyboardMarkup:
    return _inline([btn("⬅️ Меню", "a:menu")])


def stats_text(data: dict[str, Any]) -> str:
    by_status = data.get("by_status", {})
    total = sum(by_status.values())
    lines = [
        "📊 <b>Статистика поддержки</b>",
        "",
        f"Пользователей в боте: <b>{data.get('users', 0)}</b>",
        f"Всего заявок: <b>{total}</b> (сегодня: {data.get('today', 0)})",
        f"  🆕 новых: {by_status.get(db.STATUS_NEW, 0)}",
        f"  ⏳ в работе: {by_status.get(db.STATUS_IN_PROGRESS, 0)}",
        f"  ✅ закрытых: {by_status.get(db.STATUS_CLOSED, 0)}",
        "",
        "<b>По отделам</b>",
    ]
    by_dept = data.get("by_dept", {})
    for code, dept in config.DEPARTMENTS.items():
        counts = by_dept.get(code, {})
        open_n = counts.get(db.STATUS_NEW, 0) + counts.get(db.STATUS_IN_PROGRESS, 0)
        closed_n = counts.get(db.STATUS_CLOSED, 0)
        lines.append(f"{dept.emoji} {esc(dept.short)}: открыто {open_n}, закрыто {closed_n}")

    avg = data.get("avg_close_hours")
    if avg is not None:
        lines += ["", f"Среднее время закрытия: <b>{avg:.1f} ч</b>"]

    queries = data.get("queries", 0)
    answered = data.get("queries_answered", 0)
    share = f"{answered / queries * 100:.0f}%" if queries else "—"
    lines += [
        "",
        "<b>База знаний</b>",
        f"Вопросов задано: {queries}, бот ответил сам: {answered} ({share})",
    ]

    top = data.get("top_topics") or []
    if top:
        lines += ["", "<b>Самые частые темы</b>"]
        for row in top:
            item = kb.get(row["matched_id"])
            title = item.question if item else row["matched_id"]
            lines.append(f"• {esc(title)} — {row['n']}")
    return "\n".join(lines)


def unanswered_text(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return (
            "🕳 <b>Вопросы без ответа</b>\n\n"
            "Пока таких нет — база знаний закрывает все обращения. Хороший знак."
        )
    lines = [
        "🕳 <b>Вопросы, на которые бот не ответил</b>",
        "",
        "Готовый список для пополнения базы знаний "
        "(файл <code>data/faq.json</code>):",
        "",
    ]
    for row in rows:
        lines.append(
            f"• «{esc(row['query'])}» — {row['n']} раз, последний {esc(row['last_at'])}"
        )
    return "\n".join(lines)
