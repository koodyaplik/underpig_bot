import asyncio
import logging
import os
import tempfile

from aiogram import Bot
from aiogram import Dispatcher
from aiogram import F

from aiogram.types import Message

from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]

MAX_DURATION = int(os.getenv("MAX_DURATION", "900"))

MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3")

print("Loading model...")

model = WhisperModel(
    MODEL_NAME,
    device="cpu",
    compute_type="int8"
)

print("Model loaded.")

bot = Bot(TOKEN)

dp = Dispatcher()


def recognize(path):

    segments, info = model.transcribe(
        path,
        beam_size=5,
        vad_filter=True
    )

    text = ""

    for s in segments:
        text += s.text + " "

    return text.strip()


@dp.message(F.voice)
async def voice(message: Message):

    if message.voice.duration > MAX_DURATION:

        await message.reply("Голосовое слишком длинное.")

        return

    wait = await message.reply("Распознаю...")

    with tempfile.NamedTemporaryFile(suffix=".ogg") as f:

        file = await bot.get_file(message.voice.file_id)

        await bot.download_file(
            file.file_path,
            destination=f.name
        )

        text = await asyncio.to_thread(
            recognize,
            f.name
        )

    await wait.delete()

    if text == "":

        text = "(ничего не удалось распознать)"

    while len(text):

        await message.reply(text[:4000])

        text = text[4000:]


async def main():

    await dp.start_polling(bot)


asyncio.run(main())
