from __future__ import annotations

from datetime import datetime

from app.domain.models import FlightCandidate


def match_tracked_instance(
    candidates: list[FlightCandidate],
    *,
    provider_flight_id: str | None,
    flight_iata: str,
    flight_date: str,
    departure_iata: str,
    arrival_iata: str,
    identity_scheduled_local: str | None,
) -> FlightCandidate | None:
    pool = candidates
    if provider_flight_id:
        by_provider_id = [
            item for item in candidates if item.provider_flight_id == provider_flight_id
        ]
        if len(by_provider_id) == 1:
            return by_provider_id[0]
        if by_provider_id:
            pool = by_provider_id

    matches = [
        item
        for item in pool
        if item.provider_flight_iata == flight_iata
        and item.flight_date == flight_date
        and item.departure_iata == departure_iata
        and item.arrival_iata == arrival_iata
    ]
    if not matches:
        if provider_flight_id and pool is not candidates:
            return pool[0]
        return None
    if len(matches) == 1:
        return matches[0]
    exact_schedule = [
        item
        for item in matches
        if item.scheduled_departure
        and item.scheduled_departure.local_iso == identity_scheduled_local
    ]
    if exact_schedule:
        return exact_schedule[0]

    try:
        reference_wall = (
            datetime.fromisoformat(identity_scheduled_local).replace(tzinfo=None)
            if identity_scheduled_local
            else None
        )
    except ValueError:
        reference_wall = None

    def distance(item: FlightCandidate) -> int:
        if not item.scheduled_departure or not item.scheduled_departure.local_iso:
            return 2**62
        if reference_wall is None:
            return 2**61
        try:
            item_wall = datetime.fromisoformat(item.scheduled_departure.local_iso).replace(
                tzinfo=None
            )
        except ValueError:
            return 2**62
        return int(abs((item_wall - reference_wall).total_seconds()))

    matches.sort(
        key=lambda item: (
            distance(item),
            item.scheduled_departure.local_iso if item.scheduled_departure else "",
        )
    )
    return matches[0]
