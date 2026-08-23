from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.aviationstack.errors import QuotaExceededError
from app.config import Settings
from app.storage.db import Database


class QuotaManager:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._lock = asyncio.Lock()

    def billing_cycle_start_epoch(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        day = self.settings.aviationstack_billing_cycle_day
        if now.day >= day:
            start = datetime(now.year, now.month, day, tzinfo=UTC)
        elif now.month == 1:
            start = datetime(now.year - 1, 12, day, tzinfo=UTC)
        else:
            start = datetime(now.year, now.month - 1, day, tzinfo=UTC)
        return int(start.timestamp())

    async def usage(self) -> int:
        return await self.db.count_api_requests_since(self.billing_cycle_start_epoch())

    async def reserve_request(
        self,
        *,
        endpoint_name: str,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> int:
        async with self._lock:
            used = await self.usage()
            hard_cap = self.settings.aviationstack_hard_request_cap
            if used >= hard_cap and not self.settings.aviationstack_allow_overage:
                raise QuotaExceededError()
            regular_limit = (
                self.settings.aviationstack_monthly_request_limit
                - self.settings.aviationstack_request_reserve
            )
            if used >= regular_limit and priority < 80:
                raise QuotaExceededError("Only the protected Aviationstack reserve remains")
            return await self.db.start_api_request(
                endpoint_name=endpoint_name,
                flight_id=flight_id,
                trigger_type=trigger_type,
                priority=priority,
            )

    async def can_admit_forecast(self, estimated_requests: int) -> bool:
        used = await self.usage()
        regular_limit = (
            self.settings.aviationstack_monthly_request_limit
            - self.settings.aviationstack_request_reserve
        )
        return used + max(estimated_requests, 1) <= regular_limit

    async def reserve_only(self) -> bool:
        used = await self.usage()
        regular_limit = (
            self.settings.aviationstack_monthly_request_limit
            - self.settings.aviationstack_request_reserve
        )
        return used >= regular_limit
