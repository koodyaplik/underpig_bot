from __future__ import annotations

from app.aeroapi.time_parser import parse_aeroapi_time


def test_utc_timestamp_is_converted_to_airport_timezone() -> None:
    parsed = parse_aeroapi_time("2026-08-23T13:15:00Z", "Europe/Moscow")
    assert parsed is not None
    assert parsed.local_iso == "2026-08-23T16:15:00+03:00"
    assert parsed.utc_epoch == 1787490900
    assert parsed.parse_method == "aeroapi_iso8601"


def test_invalid_timezone_uses_raw_offset() -> None:
    parsed = parse_aeroapi_time("2026-08-23T13:15:00Z", "Invalid/Timezone")
    assert parsed is not None
    assert parsed.parse_method == "aeroapi_iso8601"
    assert parsed.confidence == "low"
    assert parsed.utc_epoch is not None


def test_null_time_is_allowed() -> None:
    assert parse_aeroapi_time(None, "Europe/Moscow") is None
