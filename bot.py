"""Точка входа бота технической поддержки «Продаем все на свете».

Запуск:  python bot.py
Перед первым запуском скопируйте .env.example в .env и заполните BOT_TOKEN.
"""

from __future__ import annotations

import logging
import sys

import telebot
from telebot import types

import config
import db
import handlers_admin
import handlers_user
from kb import kb

log = logging.getLogger("support-bot")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if config.LOG_FILE:
        handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s  %(levelname)-8s %(name)s  %(message)s",
        handlers=handlers,
    )
    # Телебот шумит на уровне INFO — приглушаем.
    logging.getLogger("TeleBot").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def set_commands(bot: telebot.TeleBot) -> None:
    """Подсказки команд в меню Telegram."""
    try:
        bot.set_my_commands(
            [
                types.BotCommand("start", "Начать заново"),
                types.BotCommand("faq", "Частые вопросы"),
                types.BotCommand("ticket", "Написать специалисту"),
                types.BotCommand("my", "Мои заявки"),
                types.BotCommand("about", "Контакты и режим работы"),
                types.BotCommand("help", "Справка"),
                types.BotCommand("cancel", "Отменить действие"),
                types.BotCommand("id", "Показать свой Telegram ID"),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — не критично для работы бота
        log.warning("Не удалось установить меню команд: %s", exc)


def build_bot() -> telebot.TeleBot:
    bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML", threaded=True)

    # Порядок важен: обработчики сотрудников идут первыми, иначе их пошаговые
    # диалоги перехватит общий обработчик текста из пользовательского модуля.
    handlers_admin.register(bot)
    handlers_user.register(bot)
    return bot


def main() -> int:
    setup_logging()

    try:
        warnings = config.validate()
    except config.ConfigError as exc:
        log.error("Ошибка конфигурации: %s", exc)
        return 1
    for warning in warnings:
        log.warning(warning)

    try:
        kb.load()
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось загрузить базу знаний %s: %s", config.FAQ_PATH, exc)
        return 1

    db.connect()

    bot = build_bot()
    set_commands(bot)

    try:
        me = bot.get_me()
        log.info("Бот запущен: @%s (%s)", me.username, me.first_name)
    except Exception as exc:  # noqa: BLE001
        log.error("Telegram не принял токен: %s", exc)
        db.close()
        return 1

    log.info(
        "Сотрудники: суперадмины %s, технический отдел %s, отдел продаж %s",
        config.ADMIN_IDS or "—",
        config.IT_STAFF_IDS or "—",
        config.SALES_STAFF_IDS or "—",
    )
    log.info(
        "Распознавание голоса: %s",
        f"включено ({config.STT_PROVIDER})" if config.stt_enabled() else "отключено",
    )

    try:
        # skip_pending=False: сообщения, пришедшие при выключенном боте,
        # обрабатываются после запуска — обращение клиента не пропадёт.
        bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=False)
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
    finally:
        db.close()
        log.info("Бот остановлен")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
