from __future__ import annotations

from app.aviationstack.time_parser import parse_aviationstack_time


def test_wall_clock_timezone_quirk() -> None:
    parsed = parse_aviationstack_time(
        "2026-08-23T13:15:00+00:00", "Europe/Moscow", mode="wall_clock"
    )
    assert parsed is not None
    assert parsed.local_iso == "2026-08-23T13:15:00+03:00"
    assert parsed.utc_epoch == 1787480100
    assert parsed.parse_method == "wall_clock_airport_timezone"


def test_invalid_timezone_uses_raw_offset() -> None:
    parsed = parse_aviationstack_time(
        "2026-08-23T13:15:00+00:00", "Invalid/Timezone", mode="wall_clock"
    )
    assert parsed is not None
    assert parsed.parse_method == "raw_offset_fallback"
    assert parsed.confidence == "low"
    assert parsed.utc_epoch is not None


def test_null_time_is_allowed() -> None:
    assert parse_aviationstack_time(None, "Europe/Moscow") is None
