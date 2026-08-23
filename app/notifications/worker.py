from __future__ import annotations

import asyncio
import json
import logging
import time

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from app.storage.db import Database

LOGGER = logging.getLogger(__name__)


class NotificationWorker:
    def __init__(self, *, bot: Bot, db: Database) -> None:
        self.bot = bot
        self.db = db
        self.stop_event = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.db.heartbeat("notification_worker")
                deliveries = await self.db.get_due_deliveries(limit=50)
                for delivery in deliveries:
                    if self.stop_event.is_set():
                        break
                    await self._send(dict(delivery))
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "Notification worker iteration failed",
                    extra={"event": "notification_worker_error"},
                )
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=2)
            except TimeoutError:
                continue

    async def _send(self, delivery: dict) -> None:
        delivery_id = int(delivery["id"])
        chat_id = int(delivery["telegram_chat_id"])
        payload = json.loads(str(delivery["payload_json"]))
        text = str(payload.get("text") or "")
        try:
            message = await self.bot.send_message(chat_id, text, parse_mode="HTML")
            await self.db.mark_delivery_sent(delivery_id, message.message_id)
        except TelegramRetryAfter as exc:
            await self.db.mark_delivery_retry(
                delivery_id,
                next_attempt_at_epoch=int(time.time()) + max(1, int(exc.retry_after)),
                error_code="telegram_retry_after",
            )
        except (TelegramNetworkError, TelegramServerError):
            attempts = int(delivery["attempt_count"]) + 1
            delay = min(60 * (2 ** min(attempts, 6)), 3600)
            await self.db.mark_delivery_retry(
                delivery_id,
                next_attempt_at_epoch=int(time.time()) + delay,
                error_code="telegram_temporary_error",
            )
        except TelegramForbiddenError:
            await self.db.mark_delivery_failed(delivery_id, error_code="telegram_forbidden")
            await self.db.deactivate_chat(chat_id, reason="bot_blocked")
        except TelegramBadRequest as exc:
            code = (
                "telegram_chat_not_found"
                if "chat not found" in str(exc).lower()
                else "telegram_bad_request"
            )
            await self.db.mark_delivery_failed(delivery_id, error_code=code)
            if code == "telegram_chat_not_found":
                await self.db.deactivate_chat(chat_id, reason=code)
            LOGGER.warning(
                "Permanent Telegram delivery error",
                extra={
                    "event": "telegram_delivery_failed",
                    "subscription_id": delivery["subscription_id"],
                },
            )
