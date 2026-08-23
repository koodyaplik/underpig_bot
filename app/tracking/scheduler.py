from __future__ import annotations

import asyncio
import logging
import time
import uuid

from app.config import Settings
from app.storage.db import Database
from app.tracking.quota import QuotaManager
from app.tracking.service import TrackingService

LOGGER = logging.getLogger(__name__)


class FlightScheduler:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        service: TrackingService,
        quota: QuotaManager,
    ) -> None:
        self.settings = settings
        self.db = db
        self.service = service
        self.quota = quota
        self.owner = f"scheduler-{uuid.uuid4().hex[:12]}"
        self.stop_event = asyncio.Event()
        self._semaphore = asyncio.Semaphore(settings.aviationstack_max_concurrency)

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.db.heartbeat("scheduler")
                if not await self.quota.reserve_only():
                    await self.db.resume_suspended_quota(next_check_at_epoch=int(time.time()) + 30)
                rows = await self.db.claim_due_flights(
                    owner=self.owner,
                    now_epoch=int(time.time()),
                    lease_seconds=self.settings.flight_lease_seconds,
                    limit=self.settings.scheduler_batch_size,
                )
                if rows:
                    await asyncio.gather(*(self._process(row) for row in rows))
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Scheduler iteration failed", extra={"event": "scheduler_error"})
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.settings.scheduler_tick_seconds
                )
            except TimeoutError:
                continue

    async def _process(self, row: object) -> None:
        async with self._semaphore:
            flight_id = int(row["id"])
            try:
                await self.service.process_poll(row, owner=self.owner)
            except asyncio.CancelledError:
                await self.db.release_lease(
                    flight_id,
                    owner=self.owner,
                    next_check_at_epoch=int(time.time()) + 30,
                )
                raise
            except Exception:
                LOGGER.exception(
                    "Unhandled flight processing error",
                    extra={"event": "flight_processing_error", "flight_id": flight_id},
                )
                await self.db.release_lease(
                    flight_id,
                    owner=self.owner,
                    next_check_at_epoch=int(time.time()) + 300,
                )
