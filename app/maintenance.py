from __future__ import annotations

import asyncio
import logging
import time

from app.config import Settings
from app.storage.db import Database

LOGGER = logging.getLogger(__name__)


class MaintenanceWorker:
    def __init__(self, *, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.stop_event = asyncio.Event()

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            now = int(time.time())
            day = 86400
            try:
                await self.db.cleanup(
                    pending_before=now,
                    raw_before=now - self.settings.raw_flight_json_retention_days * day,
                    api_before=now - self.settings.api_request_log_retention_days * day,
                    deliveries_before=now
                    - self.settings.notification_delivery_retention_days * day,
                    finished_before=now - self.settings.finished_flights_retention_days * day,
                )
                await self.db.heartbeat("maintenance")
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Maintenance failed", extra={"event": "maintenance_error"})
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=6 * 3600)
            except TimeoutError:
                continue
