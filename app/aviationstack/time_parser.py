from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.models import ParsedTime


def _parse_iso(raw: str) -> datetime:
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


def parse_aviationstack_time(
    raw_timestamp: object,
    airport_timezone: object,
    *,
    mode: str = "wall_clock",
) -> ParsedTime | None:
    if raw_timestamp is None or raw_timestamp == "":
        return None
    if not isinstance(raw_timestamp, str):
        return ParsedTime(
            raw=str(raw_timestamp),
            timezone=str(airport_timezone) if airport_timezone else None,
            local_iso=None,
            utc_epoch=None,
            parse_method="invalid_type",
            confidence="none",
            error="timestamp is not a string",
        )
    timezone_name = airport_timezone if isinstance(airport_timezone, str) else None
    try:
        parsed = _parse_iso(raw_timestamp)
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

    if mode == "iso8601" and parsed.tzinfo is not None:
        utc_value = parsed.astimezone(UTC)
        if timezone_name:
            try:
                local_value = utc_value.astimezone(ZoneInfo(timezone_name))
            except ZoneInfoNotFoundError:
                local_value = parsed
        else:
            local_value = parsed
        return ParsedTime(
            raw=raw_timestamp,
            timezone=timezone_name,
            local_iso=local_value.isoformat(),
            utc_epoch=int(utc_value.timestamp()),
            parse_method="iso8601",
            confidence="high",
        )

    if timezone_name:
        try:
            zone = ZoneInfo(timezone_name)
            wall = parsed.replace(tzinfo=None)
            fold0 = wall.replace(tzinfo=zone, fold=0)
            fold1 = wall.replace(tzinfo=zone, fold=1)
            ambiguous = fold0.utcoffset() != fold1.utcoffset()
            chosen = fold0
            confidence = "high"
            method = "wall_clock_airport_timezone"
            if ambiguous:
                raw_offset = parsed.utcoffset()
                if raw_offset == fold1.utcoffset():
                    chosen = fold1
                    method = "wall_clock_dst_fold_from_offset"
                elif raw_offset == fold0.utcoffset():
                    method = "wall_clock_dst_fold_from_offset"
                else:
                    confidence = "low"
                    method = "wall_clock_dst_ambiguous"
            roundtrip = chosen.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
            if roundtrip != wall:
                confidence = "low"
                method = "wall_clock_dst_nonexistent"
            return ParsedTime(
                raw=raw_timestamp,
                timezone=timezone_name,
                local_iso=chosen.isoformat(),
                utc_epoch=int(chosen.astimezone(UTC).timestamp()),
                parse_method=method,
                confidence=confidence,
            )
        except ZoneInfoNotFoundError:
            pass

    if parsed.tzinfo is not None:
        return ParsedTime(
            raw=raw_timestamp,
            timezone=timezone_name,
            local_iso=parsed.isoformat(),
            utc_epoch=int(parsed.astimezone(UTC).timestamp()),
            parse_method="raw_offset_fallback",
            confidence="low",
            error="missing or invalid airport timezone",
        )
    return ParsedTime(
        raw=raw_timestamp,
        timezone=timezone_name,
        local_iso=parsed.isoformat(),
        utc_epoch=None,
        parse_method="naive_fallback",
        confidence="none",
        error="no timezone or UTC offset available",
    )
