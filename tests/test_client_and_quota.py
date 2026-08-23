from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.aviationstack.client import AviationstackClient, split_flight_iata
from app.aviationstack.errors import AviationstackError, QuotaExceededError
from app.config import Settings
from app.storage.db import Database
from app.tracking.quota import QuotaManager


@pytest.mark.asyncio
async def test_client_records_attempt_and_redacts_storage(tmp_path: Path) -> None:
    settings = Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        aviationstack_api_key="very-secret-key",
        database_path=str(tmp_path / "test.db"),
        aviationstack_monthly_request_limit=10,
        aviationstack_request_reserve=1,
        aviationstack_hard_request_cap=10,
        _env_file=None,
    )
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["access_key"] == "very-secret-key"
        return httpx.Response(200, json={"pagination": {}, "data": []})

    quota = QuotaManager(db, settings)
    client = AviationstackClient(
        settings=settings,
        db=db,
        quota=quota,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.initialize()
        payload = await client.search_flights(
            "FV6106", flight_date=None, flight_id=None, trigger_type="test", priority=10
        )
        assert payload["data"] == []
        assert await quota.usage() == 1
        row = await db.fetchone("SELECT endpoint_name, api_error_code FROM api_requests")
        assert dict(row) == {"endpoint_name": "flights", "api_error_code": None}
    finally:
        await client.close()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_date_filter", [False, True])
async def test_flight_date_query_filter_is_configurable(
    tmp_path: Path, use_date_filter: bool
) -> None:
    settings = Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        aviationstack_api_key="test-key",
        aviationstack_use_flight_date_filter=use_date_filter,
        database_path=str(tmp_path / f"filter-{use_date_filter}.db"),
        aviationstack_monthly_request_limit=10,
        aviationstack_request_reserve=1,
        aviationstack_hard_request_cap=10,
        _env_file=None,
    )
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        assert ("flight_date" in request.url.params) is use_date_filter
        if use_date_filter:
            assert request.url.params["flight_date"] == "2026-08-23"
        return httpx.Response(200, json={"pagination": {}, "data": []})

    client = AviationstackClient(
        settings=settings,
        db=db,
        quota=QuotaManager(db, settings),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.search_flights(
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
    settings = Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        aviationstack_api_key="key",
        database_path=str(tmp_path / "test.db"),
        aviationstack_monthly_request_limit=2,
        aviationstack_request_reserve=1,
        aviationstack_hard_request_cap=2,
        _env_file=None,
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
    settings = Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        aviationstack_api_key="test-key",
        database_path=str(tmp_path / "test.db"),
        aviationstack_monthly_request_limit=10,
        aviationstack_request_reserve=1,
        aviationstack_hard_request_cap=10,
        _env_file=None,
    )
    db = Database(settings.database_path)
    await db.connect()
    await db.migrate()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>Forbidden</html>")

    client = AviationstackClient(
        settings=settings,
        db=db,
        quota=QuotaManager(db, settings),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(AviationstackError) as error:
            await client.search_flights(
                "FV6106",
                flight_date="2026-08-23",
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
    assert split_flight_iata("5N123") == ("5N", "123")


def test_legacy_bot_token_environment_name_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:LEGACY_TOKEN")
    monkeypatch.setenv("AVIATIONSTACK_API_KEY", "test-key")

    settings = Settings(
        database_path="test.db",
        aviationstack_monthly_request_limit=100,
        aviationstack_request_reserve=10,
        aviationstack_hard_request_cap=100,
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
    monkeypatch.setenv("AVIATIONSTACK_API_KEY", "test-key")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", admin_ids)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", allowed_ids)

    settings = Settings(
        database_path="test.db",
        aviationstack_monthly_request_limit=100,
        aviationstack_request_reserve=10,
        aviationstack_hard_request_cap=100,
        _env_file=None,
    )

    assert settings.telegram_admin_user_ids == expected_admins
    assert settings.telegram_allowed_user_ids == expected_allowed
