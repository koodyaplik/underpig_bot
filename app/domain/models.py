from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def provider_status_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("raw")
    values = [payload.get("provider_status")]
    if isinstance(raw, dict):
        values.append(raw.get("status"))
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


@dataclass(slots=True, frozen=True)
class ParsedTime:
    raw: str | None
    timezone: str | None
    local_iso: str | None
    utc_epoch: int | None
    parse_method: str
    confidence: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> ParsedTime | None:
        return cls(**value) if value else None


@dataclass(slots=True)
class FlightCandidate:
    requested_flight_iata: str
    provider_flight_iata: str
    flight_date: str
    airline_name: str | None
    departure_airport: str | None
    departure_iata: str
    departure_icao: str | None
    departure_timezone: str | None
    arrival_airport: str | None
    arrival_iata: str
    arrival_icao: str | None
    arrival_timezone: str | None
    scheduled_departure: ParsedTime | None
    estimated_departure: ParsedTime | None
    actual_departure: ParsedTime | None
    scheduled_arrival: ParsedTime | None
    estimated_arrival: ParsedTime | None
    actual_arrival: ParsedTime | None
    departure_delay: int | None
    arrival_delay: int | None
    departure_terminal: str | None
    departure_gate: str | None
    arrival_terminal: str | None
    arrival_gate: str | None
    arrival_baggage: str | None
    api_status: str | None
    aircraft_registration: str | None
    aircraft_iata: str | None
    aircraft_icao: str | None
    aircraft_icao24: str | None
    codeshare: dict[str, Any] | None
    source_updated_at: str | None
    raw: dict[str, Any]
    provider_flight_id: str | None = None
    source_kind: str = "realtime"
    provider_status: str | None = None
    actual_takeoff: ParsedTime | None = None
    actual_landing: ParsedTime | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FlightCandidate:
        copied = dict(value)
        copied.setdefault("provider_flight_id", None)
        copied.setdefault("source_kind", "realtime")
        copied["provider_status"] = provider_status_from_payload(copied)
        copied.setdefault("actual_takeoff", None)
        copied.setdefault("actual_landing", None)
        for field_name in (
            "scheduled_departure",
            "estimated_departure",
            "actual_departure",
            "scheduled_arrival",
            "estimated_arrival",
            "actual_arrival",
            "actual_takeoff",
            "actual_landing",
        ):
            copied[field_name] = ParsedTime.from_dict(copied.get(field_name))
        return cls(**copied)

    @property
    def effective_departure_epoch(self) -> int | None:
        for value in (self.estimated_departure, self.scheduled_departure):
            if value and value.utc_epoch is not None:
                return value.utc_epoch
        return None

    @property
    def effective_arrival_epoch(self) -> int | None:
        for value in (self.estimated_arrival, self.scheduled_arrival):
            if value and value.utc_epoch is not None:
                return value.utc_epoch
        return None


@dataclass(slots=True, frozen=True)
class PollDecision:
    next_check_at_epoch: int
    reason: str
    priority: int
    uses_reserve: bool = False


@dataclass(slots=True)
class SubscriptionResult:
    status: str
    message: str
    candidate: FlightCandidate | None = None
    candidates: list[FlightCandidate] | None = None
    subscription_id: int | None = None
