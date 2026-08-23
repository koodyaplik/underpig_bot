from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.models import ParsedTime


def parse_aeroapi_time(raw_timestamp: object, airport_timezone: object) -> ParsedTime | None:
    if raw_timestamp is None or raw_timestamp == "":
        return None
    timezone_name = airport_timezone if isinstance(airport_timezone, str) else None
    if not isinstance(raw_timestamp, str):
        return ParsedTime(
            raw=str(raw_timestamp),
            timezone=timezone_name,
            local_iso=None,
            utc_epoch=None,
            parse_method="invalid_type",
            confidence="none",
            error="timestamp is not a string",
        )
    normalized = raw_timestamp.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        return ParsedTime(
            raw=raw_timestamp,
            timezone=timezone_name,
            local_iso=None,
            utc_epoch=None,
            parse_method="invalid_timestamp",
            confidence="none",
            error=str(exc),
        )
    if parsed.tzinfo is None:
        return ParsedTime(
            raw=raw_timestamp,
            timezone=timezone_name,
            local_iso=parsed.isoformat(),
            utc_epoch=None,
            parse_method="naive_timestamp",
            confidence="none",
            error="AeroAPI timestamp has no UTC offset",
        )

    utc_value = parsed.astimezone(UTC)
    local_value = utc_value
    confidence = "high"
    error: str | None = None
    if timezone_name:
        try:
            local_value = utc_value.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            confidence = "low"
            error = "invalid airport timezone"
    return ParsedTime(
        raw=raw_timestamp,
        timezone=timezone_name,
        local_iso=local_value.isoformat(),
        utc_epoch=int(utc_value.timestamp()),
        parse_method="aeroapi_iso8601",
        confidence=confidence,
        error=error,
    )
