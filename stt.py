"""Распознавание голосовых сообщений.

Модуль изолирован специально: чтобы сменить провайдера распознавания,
достаточно добавить ветку в `transcribe()` — остальной код бота не меняется.
Сейчас поддерживается OpenAI Whisper API (STT_PROVIDER=openai).
"""

from __future__ import annotations

import logging
import io

import requests

import config

log = logging.getLogger(__name__)
_local_model = None


class STTError(Exception):
    """Не удалось распознать голосовое: текст исключения пригоден для показа клиенту."""


def transcribe(bot, media) -> str:
    """Возвращает расшифровку голосового сообщения (telebot.types.Voice/Audio)."""
    if not config.stt_enabled():
        raise STTError(
            "Распознавание голосовых сообщений сейчас отключено. "
            "Напишите, пожалуйста, вопрос текстом — так я отвечу сразу."
        )

    duration = getattr(media, "duration", 0) or 0
    if duration > config.STT_MAX_DURATION:
        raise STTError(
            f"Сообщение длиннее {config.STT_MAX_DURATION} секунд — я такое не разберу. "
            "Опишите проблему короче или напишите текстом."
        )

    try:
        file_info = bot.get_file(media.file_id)
        audio_bytes = bot.download_file(file_info.file_path)
    except Exception as exc:  # noqa: BLE001 — нельзя ронять обработчик
        log.warning("Не удалось скачать голосовое сообщение: %s", exc)
        raise STTError(
            "Не смог загрузить голосовое сообщение. Попробуйте отправить ещё раз "
            "или напишите текстом."
        ) from exc

    if config.STT_PROVIDER == "local":
        return _transcribe_local(audio_bytes)
    if config.STT_PROVIDER == "openai":
        return _transcribe_openai(audio_bytes)

    raise STTError(
        "Распознавание голоса настроено некорректно. Напишите, пожалуйста, текстом."
    )


def _transcribe_local(audio_bytes: bytes) -> str:
    """Распознаёт OGG/Opus локально через faster-whisper без API-ключа."""
    global _local_model
    try:
        from faster_whisper import WhisperModel
        if _local_model is None:
            log.info("Загрузка локальной модели faster-whisper: %s", config.STT_LOCAL_MODEL)
            _local_model = WhisperModel(config.STT_LOCAL_MODEL, device="cpu", compute_type="int8")
        segments, _ = _local_model.transcribe(
            io.BytesIO(audio_bytes), language=config.STT_LANGUAGE or None, vad_filter=True
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        log.warning("Ошибка локального распознавания: %s", exc)
        raise STTError(
            "Не удалось распознать голосовое сообщение локально. "
            "При первом запуске модель скачивается из интернета; попробуйте ещё раз или напишите текстом."
        ) from exc
    if not text:
        raise STTError("Не удалось разобрать голосовое сообщение. Попробуйте записать его ещё раз.")
    return text


def _transcribe_openai(audio_bytes: bytes) -> str:
    url = f"{config.OPENAI_BASE_URL}/audio/transcriptions"
    files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
    data = {"model": config.STT_MODEL, "response_format": "json"}
    if config.STT_LANGUAGE:
        data["language"] = config.STT_LANGUAGE
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}

    try:
        response = requests.post(
            url, headers=headers, files=files, data=data, timeout=config.STT_TIMEOUT
        )
    except requests.RequestException as exc:
        log.warning("Ошибка сети при распознавании: %s", exc)
        raise STTError(
            "Не получилось связаться с сервисом распознавания речи. "
            "Напишите, пожалуйста, текстом."
        ) from exc

    if response.status_code != 200:
        log.warning("Whisper вернул %s: %s", response.status_code, response.text[:400])
        raise STTError(
            "Сервис распознавания речи временно недоступен. "
            "Напишите, пожалуйста, вопрос текстом."
        )

    try:
        text = str(response.json().get("text", "")).strip()
    except ValueError as exc:
        log.warning("Не удалось разобрать ответ Whisper: %s", response.text[:200])
        raise STTError(
            "Не удалось распознать голосовое сообщение. Напишите, пожалуйста, текстом."
        ) from exc

    if not text:
        raise STTError(
            "Я не разобрал ни слова — возможно, было тихо или слишком шумно. "
            "Попробуйте записать заново или напишите текстом."
        )
    return text
