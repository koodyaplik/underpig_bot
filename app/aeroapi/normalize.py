from __future__ import annotations

from typing import Any

from app.aeroapi.time_parser import parse_aeroapi_time
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


def _delay_minutes(value: object) -> int | None:
    seconds = _integer(value)
    return round(seconds / 60) if seconds is not None else None


def _airport_code(airport: dict[str, Any]) -> str | None:
    return _code(airport.get("code_iata") or airport.get("code") or airport.get("code_icao"))


def _provider_iata(item: dict[str, Any], requested_flight_iata: str) -> str | None:
    raw_codeshares = item.get("codeshares_iata")
    codeshares = raw_codeshares if isinstance(raw_codeshares, list) else []
    identities = {
        value
        for value in (
            _code(item.get("ident_iata")),
            _code(item.get("actual_ident_iata")),
            *(_code(value) for value in codeshares if value),
        )
        if value
    }
    if requested_flight_iata in identities:
        return requested_flight_iata
    ident = _code(item.get("ident"))
    if ident == requested_flight_iata:
        return requested_flight_iata
    return None


def _status(item: dict[str, Any]) -> str:
    if item.get("cancelled") is True:
        return "cancelled"
    if item.get("diverted") is True:
        return "diverted"
    if item.get("actual_in") or item.get("actual_on"):
        return "landed"
    if item.get("actual_out") or item.get("actual_off"):
        return "active"
    raw = (_text(item.get("status")) or "").lower()
    if "cancel" in raw:
        return "cancelled"
    if "divert" in raw:
        return "diverted"
    if any(value in raw for value in ("arrived", "landed", "gate arrival")):
        return "landed"
    if any(value in raw for value in ("en route", "airborne", "in flight")):
        return "active"
    return "scheduled"


def normalize_flight_item(
    item: object,
    *,
    requested_flight_iata: str,
    requested_date: str | None,
) -> FlightCandidate | None:
    if not isinstance(item, dict):
        return None
    provider_iata = _provider_iata(item, requested_flight_iata)
    origin = _mapping(item.get("origin"))
    destination = _mapping(item.get("destination"))
    departure_code = _airport_code(origin)
    arrival_code = _airport_code(destination)
    if not provider_iata or not departure_code or not arrival_code:
        return None

    departure_timezone = _text(origin.get("timezone"))
    arrival_timezone = _text(destination.get("timezone"))
    scheduled_departure = parse_aeroapi_time(
        item.get("scheduled_out") or item.get("scheduled_off"), departure_timezone
    )
    flight_date = requested_date
    if scheduled_departure and scheduled_departure.local_iso:
        flight_date = scheduled_departure.local_iso[:10]
    if not flight_date:
        return None

    codeshares_iata = item.get("codeshares_iata")
    codeshares_icao = item.get("codeshares")
    codeshare = {
        "iata": codeshares_iata if isinstance(codeshares_iata, list) else [],
        "icao": codeshares_icao if isinstance(codeshares_icao, list) else [],
    }
    last_position = _mapping(item.get("last_position"))
    return FlightCandidate(
        requested_flight_iata=requested_flight_iata,
        provider_flight_iata=provider_iata,
        flight_date=flight_date,
        airline_name=_text(item.get("operator")),
        departure_airport=_text(origin.get("name")),
        departure_iata=departure_code,
        departure_icao=_code(origin.get("code_icao")),
        departure_timezone=departure_timezone,
        arrival_airport=_text(destination.get("name")),
        arrival_iata=arrival_code,
        arrival_icao=_code(destination.get("code_icao")),
        arrival_timezone=arrival_timezone,
        scheduled_departure=scheduled_departure,
        estimated_departure=parse_aeroapi_time(
            item.get("estimated_out") or item.get("estimated_off"), departure_timezone
        ),
        actual_departure=parse_aeroapi_time(
            item.get("actual_out") or item.get("actual_off"), departure_timezone
        ),
        scheduled_arrival=parse_aeroapi_time(
            item.get("scheduled_in") or item.get("scheduled_on"), arrival_timezone
        ),
        estimated_arrival=parse_aeroapi_time(
            item.get("estimated_in") or item.get("estimated_on"), arrival_timezone
        ),
        actual_arrival=parse_aeroapi_time(
            item.get("actual_in") or item.get("actual_on"), arrival_timezone
        ),
        departure_delay=_delay_minutes(item.get("departure_delay")),
        arrival_delay=_delay_minutes(item.get("arrival_delay")),
        departure_terminal=_text(item.get("terminal_origin")),
        departure_gate=_text(item.get("gate_origin")),
        arrival_terminal=_text(item.get("terminal_destination")),
        arrival_gate=_text(item.get("gate_destination")),
        arrival_baggage=_text(item.get("baggage_claim")),
        api_status=_status(item),
        aircraft_registration=_code(item.get("registration")),
        aircraft_iata=None,
        aircraft_icao=_code(item.get("aircraft_type")),
        aircraft_icao24=None,
        codeshare=codeshare if codeshare["iata"] or codeshare["icao"] else None,
        source_updated_at=_text(last_position.get("timestamp")),
        raw=item,
        provider_flight_id=_text(item.get("fa_flight_id")),
        source_kind="realtime",
        provider_status=_text(item.get("status")),
    )


def normalize_flights_response(
    payload: object,
    *,
    requested_flight_iata: str,
    requested_date: str | None,
) -> list[FlightCandidate]:
    root = _mapping(payload)
    items = root.get("flights")
    if not isinstance(items, list):
        return []
    result: list[FlightCandidate] = []
    for item in items:
        candidate = normalize_flight_item(
            item,
            requested_flight_iata=requested_flight_iata,
            requested_date=requested_date,
        )
        if candidate:
            result.append(candidate)
    return result


def schedule_airport_codes(payload: object, *, requested_flight_iata: str) -> set[str]:
    root = _mapping(payload)
    items = root.get("scheduled")
    if not isinstance(items, list):
        return set()
    codes: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not _provider_iata(item, requested_flight_iata):
            continue
        for prefix in ("origin", "destination"):
            code = _code(
                item.get(f"{prefix}_icao") or item.get(f"{prefix}_iata") or item.get(prefix)
            )
            if code:
                codes.add(code)
    return codes


def _airport_metadata(
    item: dict[str, Any], prefix: str, metadata: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    for name in (f"{prefix}_icao", f"{prefix}_iata", prefix):
        code = _code(item.get(name))
        if code and code in metadata:
            return metadata[code]
    return {}


def normalize_schedule_response(
    payload: object,
    *,
    requested_flight_iata: str,
    requested_date: str,
    airport_metadata: dict[str, dict[str, Any]],
) -> list[FlightCandidate]:
    root = _mapping(payload)
    items = root.get("scheduled")
    if not isinstance(items, list):
        return []
    result: list[FlightCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_iata = _provider_iata(item, requested_flight_iata)
        if not provider_iata:
            continue
        origin_info = _airport_metadata(item, "origin", airport_metadata)
        destination_info = _airport_metadata(item, "destination", airport_metadata)
        departure_timezone = _text(origin_info.get("timezone"))
        arrival_timezone = _text(destination_info.get("timezone"))
        scheduled_departure = parse_aeroapi_time(item.get("scheduled_out"), departure_timezone)
        local_date = (
            scheduled_departure.local_iso[:10]
            if scheduled_departure and scheduled_departure.local_iso
            else None
        )
        if local_date != requested_date:
            continue
        departure_code = _code(
            item.get("origin_iata")
            or origin_info.get("code_iata")
            or item.get("origin")
            or item.get("origin_icao")
        )
        arrival_code = _code(
            item.get("destination_iata")
            or destination_info.get("code_iata")
            or item.get("destination")
            or item.get("destination_icao")
        )
        if not departure_code or not arrival_code:
            continue
        result.append(
            FlightCandidate(
                requested_flight_iata=requested_flight_iata,
                provider_flight_iata=provider_iata,
                flight_date=requested_date,
                airline_name=None,
                departure_airport=_text(origin_info.get("name")),
                departure_iata=departure_code,
                departure_icao=_code(item.get("origin_icao") or origin_info.get("code_icao")),
                departure_timezone=departure_timezone,
                arrival_airport=_text(destination_info.get("name")),
                arrival_iata=arrival_code,
                arrival_icao=_code(
                    item.get("destination_icao") or destination_info.get("code_icao")
                ),
                arrival_timezone=arrival_timezone,
                scheduled_departure=scheduled_departure,
                estimated_departure=None,
                actual_departure=None,
                scheduled_arrival=parse_aeroapi_time(item.get("scheduled_in"), arrival_timezone),
                estimated_arrival=None,
                actual_arrival=None,
                departure_delay=None,
                arrival_delay=None,
                departure_terminal=None,
                departure_gate=None,
                arrival_terminal=None,
                arrival_gate=None,
                arrival_baggage=None,
                api_status="scheduled",
                aircraft_registration=None,
                aircraft_iata=None,
                aircraft_icao=_code(item.get("aircraft_type")),
                aircraft_icao24=None,
                codeshare=None,
                source_updated_at=None,
                raw=item,
                provider_flight_id=_text(item.get("fa_flight_id")),
                source_kind="schedule",
                provider_status=_text(item.get("status")),
            )
        )
    return result


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
        "schema_version": 3,
        "provider_flight_id": candidate.provider_flight_id,
        "flight_iata": candidate.requested_flight_iata,
        "flight_date": candidate.flight_date,
        "api_status": candidate.api_status,
        "provider_status": candidate.provider_status,
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
