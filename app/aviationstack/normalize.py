from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from app.aviationstack.time_parser import parse_aviationstack_time
from app.domain.models import FlightCandidate


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _code(value: object) -> str | None:
    text = _text(value)
    return text.upper() if text else None


def _integer(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def normalize_realtime_item(
    item: object,
    *,
    requested_flight_iata: str,
    time_mode: str,
) -> FlightCandidate | None:
    if not isinstance(item, dict):
        return None
    departure = _mapping(item.get("departure"))
    arrival = _mapping(item.get("arrival"))
    flight = _mapping(item.get("flight"))
    airline = _mapping(item.get("airline"))
    aircraft = _mapping(item.get("aircraft"))
    live = _mapping(item.get("live"))

    provider_iata = _code(flight.get("iata"))
    flight_date = _text(item.get("flight_date"))
    departure_iata = _code(departure.get("iata"))
    arrival_iata = _code(arrival.get("iata"))
    if not provider_iata or not flight_date or not departure_iata or not arrival_iata:
        return None

    departure_timezone = _text(departure.get("timezone"))
    arrival_timezone = _text(arrival.get("timezone"))
    status = _text(item.get("flight_status"))
    status = status.lower() if status else None

    return FlightCandidate(
        requested_flight_iata=requested_flight_iata,
        provider_flight_iata=provider_iata,
        flight_date=flight_date,
        airline_name=_text(airline.get("name") or airline.get("airline_name")),
        departure_airport=_text(departure.get("airport")),
        departure_iata=departure_iata,
        departure_icao=_code(departure.get("icao")),
        departure_timezone=departure_timezone,
        arrival_airport=_text(arrival.get("airport")),
        arrival_iata=arrival_iata,
        arrival_icao=_code(arrival.get("icao")),
        arrival_timezone=arrival_timezone,
        scheduled_departure=parse_aviationstack_time(
            departure.get("scheduled"), departure_timezone, mode=time_mode
        ),
        estimated_departure=parse_aviationstack_time(
            departure.get("estimated"), departure_timezone, mode=time_mode
        ),
        actual_departure=parse_aviationstack_time(
            departure.get("actual"), departure_timezone, mode=time_mode
        ),
        scheduled_arrival=parse_aviationstack_time(
            arrival.get("scheduled"), arrival_timezone, mode=time_mode
        ),
        estimated_arrival=parse_aviationstack_time(
            arrival.get("estimated"), arrival_timezone, mode=time_mode
        ),
        actual_arrival=parse_aviationstack_time(
            arrival.get("actual"), arrival_timezone, mode=time_mode
        ),
        departure_delay=_integer(departure.get("delay")),
        arrival_delay=_integer(arrival.get("delay")),
        departure_terminal=_text(departure.get("terminal")),
        departure_gate=_code(departure.get("gate")),
        arrival_terminal=_text(arrival.get("terminal")),
        arrival_gate=_code(arrival.get("gate")),
        arrival_baggage=_text(arrival.get("baggage")),
        api_status=status,
        aircraft_registration=_code(aircraft.get("registration")),
        aircraft_iata=_code(aircraft.get("iata")),
        aircraft_icao=_code(aircraft.get("icao")),
        aircraft_icao24=_code(aircraft.get("icao24")),
        codeshare=_mapping(flight.get("codeshared")) or None,
        source_updated_at=_text(live.get("updated")),
        raw=item,
    )


def normalize_realtime_response(
    payload: object,
    *,
    requested_flight_iata: str,
    time_mode: str,
) -> list[FlightCandidate]:
    root = _mapping(payload)
    items = root.get("data")
    if not isinstance(items, list):
        return []
    normalized: list[FlightCandidate] = []
    for item in items:
        candidate = normalize_realtime_item(
            item,
            requested_flight_iata=requested_flight_iata,
            time_mode=time_mode,
        )
        if candidate:
            normalized.append(candidate)
    return normalized


def _flatten_future(items: object) -> Iterable[dict[str, Any]]:
    if isinstance(items, dict):
        yield items
    elif isinstance(items, list):
        for item in items:
            yield from _flatten_future(item)


def normalize_future_response(
    payload: object,
    *,
    requested_flight_iata: str,
    requested_date: str,
) -> list[FlightCandidate]:
    root = _mapping(payload)
    normalized: list[FlightCandidate] = []
    for item in _flatten_future(root.get("data")):
        departure = _mapping(item.get("departure"))
        arrival = _mapping(item.get("arrival"))
        flight = _mapping(item.get("flight"))
        codeshare = _mapping(item.get("codeshared"))
        codeshare_flight = _mapping(codeshare.get("flight"))
        airline = _mapping(item.get("airline"))
        aircraft = _mapping(item.get("aircraft"))
        provider_iata = _code(codeshare_flight.get("iataNumber") or flight.get("iataNumber"))
        dep_iata = _code(departure.get("iataCode"))
        arr_iata = _code(arrival.get("iataCode"))
        if provider_iata != requested_flight_iata or not dep_iata or not arr_iata:
            continue
        dep_time = _text(departure.get("scheduledTime"))
        arr_time = _text(arrival.get("scheduledTime"))
        arrival_date = requested_date
        if dep_time and arr_time and arr_time < dep_time:
            arrival_date = (date.fromisoformat(requested_date) + timedelta(days=1)).isoformat()
        dep_raw = f"{requested_date}T{dep_time}:00" if dep_time and len(dep_time) == 5 else None
        arr_raw = f"{arrival_date}T{arr_time}:00" if arr_time and len(arr_time) == 5 else None
        normalized.append(
            FlightCandidate(
                requested_flight_iata=requested_flight_iata,
                provider_flight_iata=provider_iata,
                flight_date=requested_date,
                airline_name=_text(airline.get("name")),
                departure_airport=None,
                departure_iata=dep_iata,
                departure_icao=_code(departure.get("icaoCode")),
                departure_timezone=None,
                arrival_airport=None,
                arrival_iata=arr_iata,
                arrival_icao=_code(arrival.get("icaoCode")),
                arrival_timezone=None,
                scheduled_departure=parse_aviationstack_time(dep_raw, None),
                estimated_departure=None,
                actual_departure=None,
                scheduled_arrival=parse_aviationstack_time(arr_raw, None),
                estimated_arrival=None,
                actual_arrival=None,
                departure_delay=None,
                arrival_delay=None,
                departure_terminal=_text(departure.get("terminal")),
                departure_gate=_code(departure.get("gate")),
                arrival_terminal=_text(arrival.get("terminal")),
                arrival_gate=_code(arrival.get("gate")),
                arrival_baggage=None,
                api_status="scheduled",
                aircraft_registration=None,
                aircraft_iata=_code(aircraft.get("modelCode")),
                aircraft_icao=None,
                aircraft_icao24=None,
                codeshare=codeshare or None,
                source_updated_at=None,
                raw=item,
                source_kind="future",
            )
        )
    return normalized


def candidate_to_state(candidate: FlightCandidate, *, fetched_at_epoch: int) -> dict[str, Any]:
    def serialized(value: object) -> object:
        return value.to_dict() if hasattr(value, "to_dict") else value

    def effective_delay(
        scheduled: object, estimated: object, actual: object, api_delay: int | None
    ) -> int | None:
        scheduled_epoch = scheduled.utc_epoch if hasattr(scheduled, "utc_epoch") else None
        actual_epoch = actual.utc_epoch if hasattr(actual, "utc_epoch") else None
        estimated_epoch = estimated.utc_epoch if hasattr(estimated, "utc_epoch") else None
        effective_epoch = actual_epoch if actual_epoch is not None else estimated_epoch
        if scheduled_epoch is not None and effective_epoch is not None:
            return round((effective_epoch - scheduled_epoch) / 60)
        return api_delay

    departure_delay = effective_delay(
        candidate.scheduled_departure,
        candidate.estimated_departure,
        candidate.actual_departure,
        candidate.departure_delay,
    )
    arrival_delay = effective_delay(
        candidate.scheduled_arrival,
        candidate.estimated_arrival,
        candidate.actual_arrival,
        candidate.arrival_delay,
    )
    return {
        "schema_version": 1,
        "flight_iata": candidate.requested_flight_iata,
        "flight_date": candidate.flight_date,
        "api_status": candidate.api_status,
        "route": {
            "departure_iata": candidate.departure_iata,
            "departure_airport": candidate.departure_airport,
            "arrival_iata": candidate.arrival_iata,
            "arrival_airport": candidate.arrival_airport,
        },
        "departure": {
            "scheduled": serialized(candidate.scheduled_departure),
            "estimated": serialized(candidate.estimated_departure),
            "actual": serialized(candidate.actual_departure),
            "delay_minutes": departure_delay,
            "terminal": candidate.departure_terminal,
            "gate": candidate.departure_gate,
        },
        "arrival": {
            "scheduled": serialized(candidate.scheduled_arrival),
            "estimated": serialized(candidate.estimated_arrival),
            "actual": serialized(candidate.actual_arrival),
            "delay_minutes": arrival_delay,
            "terminal": candidate.arrival_terminal,
            "gate": candidate.arrival_gate,
            "baggage": candidate.arrival_baggage,
        },
        "aircraft_registration": candidate.aircraft_registration,
        "source_updated_at": candidate.source_updated_at,
        "fetched_at_epoch": fetched_at_epoch,
        "source_kind": candidate.source_kind,
    }
