from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from app.aeroapi.client import AeroApiClient, split_flight_designator
from app.aeroapi.errors import AeroApiError, QuotaExceededError
from app.config import Settings
from app.storage.db import Database
from app.tracking.quota import QuotaManager


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123456:TEST_TOKEN",
        "aeroapi_api_key": "very-secret-key",
        "database_path": str(tmp_path / "test.db"),
        "aeroapi_monthly_request_limit": 10,
        "aeroapi_request_reserve": 1,
        "aeroapi_hard_request_cap": 10,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_client_sends_aeroapi_contract_and_logs_url_without_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/aeroapi/flights/FV6106"
        assert list(request.url.params.multi_items()) == [
            ("ident_type", "designator"),
            ("max_pages", "1"),
        ]
        assert request.headers["x-apikey"] == "very-secret-key"
        return httpx.Response(200, json={"links": {"next": ""}, "flights": []})

    quota = QuotaManager(db, settings)
    client = AeroApiClient(
        settings=settings,
        db=db,
        quota=quota,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.initialize()
        with caplog.at_level(logging.INFO, logger="app.aeroapi.client"):
            payload = await client.search_flights(
                "FV6106", flight_id=None, trigger_type="test", priority=10
            )
        assert payload["flights"] == []
        assert "https://aeroapi.flightaware.com/aeroapi/flights/FV6106" in caplog.text
        assert "very-secret-key" not in caplog.text
        assert await quota.usage() == 1
        row = await db.fetchone("SELECT endpoint_name, api_error_code FROM api_requests")
        assert dict(row) == {"endpoint_name": "flights_by_ident", "api_error_code": None}
    finally:
        await client.close()
        await db.close()


@pytest.mark.asyncio
async def test_schedule_request_uses_date_range_and_flight_filters(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/aeroapi/schedules/2026-08-22/2026-08-25"
        assert request.url.params["airline"] == "FV"
        assert request.url.params["flight_number"] == "6106"
        assert request.url.params["include_codeshares"] == "true"
        return httpx.Response(200, json={"scheduled": []})

    client = AeroApiClient(
        settings=settings,
        db=db,
        quota=QuotaManager(db, settings),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.search_schedules(
            "FV6106",
            flight_date="2026-08-23",
            flight_id=None,
            trigger_type="test",
            priority=10,
        )
    finally:
        await client.close()
        await db.close()


@pytest.mark.asyncio
async def test_reserve_is_protected_for_priority_requests(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        aeroapi_api_key="key",
        aeroapi_monthly_request_limit=2,
        aeroapi_request_reserve=1,
        aeroapi_hard_request_cap=2,
    )
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()
    quota = QuotaManager(db, settings)
    try:
        await quota.reserve_request(
            endpoint_name="flights", flight_id=None, trigger_type="test", priority=10
        )
        with pytest.raises(QuotaExceededError):
            await quota.reserve_request(
                endpoint_name="flights", flight_id=None, trigger_type="test", priority=10
            )
        await quota.reserve_request(
            endpoint_name="flights", flight_id=None, trigger_type="test", priority=90
        )
        with pytest.raises(QuotaExceededError):
            await quota.reserve_request(
                endpoint_name="flights", flight_id=None, trigger_type="test", priority=90
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_non_json_http_error_keeps_http_status_classification(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>Forbidden</html>")

    client = AeroApiClient(
        settings=settings,
        db=db,
        quota=QuotaManager(db, settings),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AeroApiError) as error:
            await client.search_flights(
                "FV6106",
                flight_id=None,
                trigger_type="test",
                priority=10,
            )
        assert error.value.code == "http_403"
        row = await db.fetchone("SELECT http_status, api_error_code FROM api_requests")
        assert dict(row) == {"http_status": 403, "api_error_code": "http_403"}
    finally:
        await client.close()
        await db.close()


def test_numeric_airline_designator_is_supported() -> None:
    assert split_flight_designator("5N123") == ("5N", "123")


def test_legacy_bot_token_environment_name_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:LEGACY_TOKEN")
    monkeypatch.setenv("FLIGHTAWARE_AEROAPI_KEY", "test-key")

    settings = Settings(
        database_path="test.db",
        aeroapi_monthly_request_limit=100,
        aeroapi_request_reserve=10,
        aeroapi_hard_request_cap=100,
        _env_file=None,
    )

    assert settings.telegram_token == "123456:LEGACY_TOKEN"


@pytest.mark.parametrize(
    ("admin_ids", "allowed_ids", "expected_admins", "expected_allowed"),
    [
        ("", "", [], []),
        ("1001, 1002", "2001", [1001, 1002], [2001]),
    ],
)
def test_user_id_lists_accept_env_format(
    monkeypatch: pytest.MonkeyPatch,
    admin_ids: str,
    allowed_ids: str,
    expected_admins: list[int],
    expected_allowed: list[int],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("FLIGHTAWARE_AEROAPI_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", admin_ids)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", allowed_ids)

    settings = Settings(
        database_path="test.db",
        aeroapi_monthly_request_limit=100,
        aeroapi_request_reserve=10,
        aeroapi_hard_request_cap=100,
        _env_file=None,
    )

    assert settings.telegram_admin_user_ids == expected_admins
    assert settings.telegram_allowed_user_ids == expected_allowed
