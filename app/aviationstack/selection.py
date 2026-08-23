from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.models import FlightCandidate

TERMINAL_STATUSES = {"landed", "cancelled"}


def _local_today(candidate: FlightCandidate, now: datetime) -> str | None:
    if not candidate.departure_timezone:
        return None
    try:
        return now.astimezone(ZoneInfo(candidate.departure_timezone)).date().isoformat()
    except ZoneInfoNotFoundError:
        return None


def _time_distance(candidate: FlightCandidate, now_epoch: int) -> int:
    value = candidate.effective_departure_epoch
    return abs(value - now_epoch) if value is not None else 2**62


def select_flight_candidates(
    items: list[FlightCandidate],
    *,
    requested_flight_iata: str,
    requested_date: str | None,
    now: datetime | None = None,
) -> list[FlightCandidate]:
    now = now or datetime.now(UTC)
    exact = [item for item in items if item.provider_flight_iata == requested_flight_iata]
    if requested_date:
        exact = [item for item in exact if item.flight_date == requested_date]
    else:
        today = [item for item in exact if _local_today(item, now) == item.flight_date]
        exact = today
    if not exact:
        return []

    active = [item for item in exact if item.api_status == "active"]
    if active:
        exact = active
    else:
        non_terminal = [item for item in exact if item.api_status not in TERMINAL_STATUSES]
        if non_terminal:
            exact = non_terminal

    exact.sort(
        key=lambda item: (
            item.departure_iata,
            item.arrival_iata,
            _time_distance(item, int(now.timestamp())),
            item.scheduled_departure.local_iso if item.scheduled_departure else "",
        )
    )
    unique: dict[tuple[str, str, str], FlightCandidate] = {}
    for item in exact:
        schedule = item.scheduled_departure.local_iso if item.scheduled_departure else ""
        unique.setdefault((item.departure_iata, item.arrival_iata, schedule), item)
    return list(unique.values())
