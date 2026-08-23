from __future__ import annotations

from pathlib import Path

import pytest

from app.aviationstack.time_parser import parse_aviationstack_time
from app.config import Settings
from app.domain.models import FlightCandidate


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        aviationstack_api_key="test-key",
        database_path=str(tmp_path / "flights.db"),
        aviationstack_monthly_request_limit=100,
        aviationstack_request_reserve=10,
        aviationstack_hard_request_cap=100,
        _env_file=None,
    )


def make_candidate(
    *,
    flight_date: str = "2026-08-23",
    departure_iata: str = "GOJ",
    arrival_iata: str = "LED",
    scheduled: str = "2026-08-23T13:15:00+00:00",
    estimated: str | None = None,
    status: str | None = "scheduled",
    gate: str | None = None,
    baggage: str | None = None,
    source_kind: str = "realtime",
) -> FlightCandidate:
    timezone = "Europe/Moscow"
    return FlightCandidate(
        requested_flight_iata="FV6106",
        provider_flight_iata="FV6106",
        flight_date=flight_date,
        airline_name="Rossiya",
        departure_airport="Nizhny Novgorod",
        departure_iata=departure_iata,
        departure_icao="UWGG",
        departure_timezone=timezone,
        arrival_airport="Pulkovo",
        arrival_iata=arrival_iata,
        arrival_icao="ULLI",
        arrival_timezone=timezone,
        scheduled_departure=parse_aviationstack_time(scheduled, timezone),
        estimated_departure=parse_aviationstack_time(estimated, timezone),
        actual_departure=None,
        scheduled_arrival=parse_aviationstack_time(f"{flight_date}T15:10:00+00:00", timezone),
        estimated_arrival=None,
        actual_arrival=None,
        departure_delay=None,
        arrival_delay=None,
        departure_terminal="1",
        departure_gate=gate,
        arrival_terminal=None,
        arrival_gate=None,
        arrival_baggage=baggage,
        api_status=status,
        aircraft_registration=None,
        aircraft_iata=None,
        aircraft_icao=None,
        aircraft_icao24=None,
        codeshare=None,
        source_updated_at=None,
        raw={"flight_date": flight_date},
        source_kind=source_kind,
    )
