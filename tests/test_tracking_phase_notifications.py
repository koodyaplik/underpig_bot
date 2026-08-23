from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.aeroapi.client import AeroApiClient
from app.aeroapi.normalize import normalize_flights_response
from app.config import Settings
from app.notifications.worker import NotificationWorker
from app.storage.db import Database
from app.tracking.quota import QuotaManager
from app.tracking.service import TrackingService


def _flight_payload(
    *,
    actual_off: str | None = None,
    actual_on: str | None = None,
) -> dict[str, object]:
    return {
        "flights": [
            {
                "ident": "SDM6325",
                "ident_iata": "FV6325",
                "fa_flight_id": "SDM6325-1787480000-airline-0010",
                "operator": "Rossiya",
                "origin": {
                    "code_iata": "LED",
                    "code_icao": "ULLI",
                    "timezone": "Europe/Moscow",
                    "name": "Pulkovo",
                },
                "destination": {
                    "code_iata": "SVO",
                    "code_icao": "UUEE",
                    "timezone": "Europe/Moscow",
                    "name": "Sheremetyevo",
                },
                "scheduled_out": "2026-08-23T13:00:00Z",
                "actual_out": "2026-08-23T13:04:00Z",
                "actual_off": actual_off,
                "scheduled_in": "2026-08-23T15:00:00Z",
                "estimated_in": "2026-08-23T14:50:00Z",
                "actual_on": actual_on,
                "actual_in": None,
                "status": "En Route",
            }
        ]
    }


class _RecordingBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, str | None]] = []

    async def send_message(self, chat_id: int, text: str, *, parse_mode: str | None = None):
        self.messages.append((chat_id, text, parse_mode))
        return type("TelegramMessage", (), {"message_id": len(self.messages)})()


@pytest.mark.asyncio
async def test_takeoff_and_landing_updates_reach_the_group_telegram_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_epoch = int(datetime(2026, 8, 23, 14, 21, tzinfo=UTC).timestamp())
    monkeypatch.setattr("app.tracking.service.time.time", lambda: now_epoch)

    settings = Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        aeroapi_api_key="test-key",
        database_path=str(tmp_path / "flights.db"),
        aeroapi_monthly_request_limit=100,
        aeroapi_request_reserve=10,
        aeroapi_hard_request_cap=100,
        _env_file=None,
    )
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    response_payload = _flight_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/aeroapi/flights/SDM6325-1787480000-airline-0010"
        assert request.url.params["ident_type"] == "fa_flight_id"
        return httpx.Response(200, json=response_payload)

    quota = QuotaManager(db, settings)
    client = AeroApiClient(
        settings=settings,
        db=db,
        quota=quota,
        transport=httpx.MockTransport(handler),
    )
    service = TrackingService(settings=settings, db=db, client=client, quota=quota)
    bot = _RecordingBot()
    notification_worker = NotificationWorker(bot=bot, db=db)  # type: ignore[arg-type]

    try:
        await client.initialize()
        initial_candidate = normalize_flights_response(
            _flight_payload(),
            requested_flight_iata="FV6325",
            requested_date="2026-08-23",
        )[0]
        flight_id, _ = await db.create_or_get_flight(initial_candidate)
        await db.add_subscription(flight_id, user_id=42, chat_id=-1001234567890)

        response_payload = _flight_payload(actual_off="2026-08-23T13:11:00Z")
        rows = await db.claim_due_flights(
            owner="takeoff-test",
            now_epoch=now_epoch + 1,
            lease_seconds=120,
            limit=10,
        )
        assert len(rows) == 1
        await service.process_poll(rows[0], owner="takeoff-test")

        deliveries = await db.get_due_deliveries()
        assert len(deliveries) == 1
        await notification_worker._send(dict(deliveries[0]))
        assert bot.messages[0][0] == -1001234567890
        assert "Взлетел:" in bot.messages[0][1]
        assert bot.messages[0][2] == "HTML"

        await db.set_flight_schedule(flight_id, next_epoch=0, priority=100)
        response_payload = _flight_payload(
            actual_off="2026-08-23T13:11:00Z",
            actual_on="2026-08-23T14:18:00Z",
        )
        rows = await db.claim_due_flights(
            owner="landing-test",
            now_epoch=now_epoch + 1,
            lease_seconds=120,
            limit=10,
        )
        assert len(rows) == 1
        await service.process_poll(rows[0], owner="landing-test")

        deliveries = await db.get_due_deliveries()
        assert len(deliveries) == 1
        await notification_worker._send(dict(deliveries[0]))
        assert bot.messages[1][0] == -1001234567890
        assert "Приземлился:" in bot.messages[1][1]
        assert bot.messages[1][2] == "HTML"
    finally:
        await client.close()
        await db.close()
