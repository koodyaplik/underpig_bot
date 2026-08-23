from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Message

from app.config import Settings
from app.voice.transcriber import VoiceTranscriber

LOGGER = logging.getLogger(__name__)
TELEGRAM_TEXT_CHUNK = 4000


def build_voice_router(*, settings: Settings, bot: Bot, transcriber: VoiceTranscriber) -> Router:
    router = Router(name="voice_transcription")

    @router.message(F.voice)
    async def transcribe_voice(message: Message) -> None:
        voice = message.voice
        if voice is None:
            return
        if voice.duration > settings.max_duration:
            await message.reply(
                f"Голосовое слишком длинное. Максимум — {settings.max_duration} секунд."
            )
            return

        progress = await message.reply("Распознаю…")
        try:
            with tempfile.TemporaryDirectory(prefix="underpig-voice-") as directory:
                target = Path(directory) / "voice.ogg"
                telegram_file = await bot.get_file(voice.file_id)
                if not telegram_file.file_path:
                    raise RuntimeError("Telegram did not return a voice file path")
                await bot.download_file(telegram_file.file_path, destination=target)
                text = await transcriber.transcribe(target)
        except Exception:
            LOGGER.exception(
                "Voice transcription failed",
                extra={"event": "voice_transcription_failed", "chat_id": message.chat.id},
            )
            await progress.edit_text("Не удалось распознать голосовое сообщение. Попробуйте позже.")
            return

        chunks = _split_text(text or "(ничего не удалось распознать)")
        await progress.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await message.reply(chunk)

    return router


def _split_text(text: str) -> list[str]:
    return [
        text[index : index + TELEGRAM_TEXT_CHUNK]
        for index in range(0, len(text), TELEGRAM_TEXT_CHUNK)
    ]
