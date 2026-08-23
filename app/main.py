from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand

from app.aeroapi.client import AeroApiClient
from app.bot.handlers import build_router
from app.bot.voice_handlers import build_voice_router
from app.config import Settings
from app.logging_setup import configure_logging
from app.maintenance import MaintenanceWorker
from app.notifications.worker import NotificationWorker
from app.storage.db import Database
from app.tracking.quota import QuotaManager
from app.tracking.scheduler import FlightScheduler
from app.tracking.service import TrackingService
from app.voice.transcriber import VoiceTranscriber

LOGGER = logging.getLogger(__name__)


async def run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level, settings.log_format)
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    quota = QuotaManager(db, settings)
    client = AeroApiClient(settings=settings, db=db, quota=quota)
    await client.initialize()
    telegram_session = (
        AiohttpSession(proxy=settings.telegram_proxy)
        if settings.telegram_proxy
        else AiohttpSession()
    )
    bot = Bot(settings.telegram_token, session=telegram_session)
    tracking = TrackingService(settings=settings, db=db, client=client, quota=quota)
    scheduler = FlightScheduler(settings=settings, db=db, service=tracking, quota=quota)
    notifier = NotificationWorker(bot=bot, db=db)
    maintenance = MaintenanceWorker(settings=settings, db=db)

    dispatcher = Dispatcher()
    if settings.voice_transcription_enabled:
        transcriber = VoiceTranscriber(
            model_name=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            cache_dir=settings.whisper_cache_dir,
        )
        dispatcher.include_router(
            build_voice_router(settings=settings, bot=bot, transcriber=transcriber)
        )
    dispatcher.include_router(build_router(settings=settings, db=db, tracking=tracking))
    await bot.set_my_commands(
        [
            BotCommand(command="flight", description="Добавить рейс"),
            BotCommand(command="flights", description="Мои отслеживаемые рейсы"),
            BotCommand(command="stop", description="Остановить отслеживание"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="delete_me", description="Удалить мои данные"),
        ]
    )

    tasks = [
        asyncio.create_task(scheduler.run(), name="flight-scheduler"),
        asyncio.create_task(notifier.run(), name="notification-worker"),
        asyncio.create_task(maintenance.run(), name="maintenance-worker"),
    ]
    try:
        LOGGER.info("Bot started", extra={"event": "application_started"})
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        scheduler.stop()
        notifier.stop()
        maintenance.stop()
        try:
            async with asyncio.timeout(20):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()
        await bot.session.close()
        await db.close()
        LOGGER.info("Bot stopped", extra={"event": "application_stopped"})


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
