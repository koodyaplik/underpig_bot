import asyncio
import os

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


async def check_telegram() -> None:
    proxy = os.getenv("TELEGRAM_PROXY")
    session = AiohttpSession(proxy=proxy) if proxy else AiohttpSession()

    async with Bot(token=os.environ["BOT_TOKEN"], session=session) as bot:
        await bot.get_me()


asyncio.run(check_telegram())
