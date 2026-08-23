from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_candidate

from app.aeroapi.matching import match_tracked_instance
from app.aeroapi.selection import select_flight_candidates


def test_selection_does_not_depend_on_order() -> None:
    yesterday = make_candidate(flight_date="2026-08-22")
    today = make_candidate(flight_date="2026-08-23")
    now = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    assert select_flight_candidates(
        [yesterday, today],
        requested_flight_iata="FV6106",
        requested_date="2026-08-23",
        now=now,
    ) == [today]
    assert select_flight_candidates(
        [today, yesterday],
        requested_flight_iata="FV6106",
        requested_date="2026-08-23",
        now=now,
    ) == [today]


def test_active_is_preferred_over_landed() -> None:
    active = make_candidate(status="active")
    landed = make_candidate(status="landed")
    selected = select_flight_candidates(
        [landed, active],
        requested_flight_iata="FV6106",
        requested_date="2026-08-23",
    )
    assert selected == [active]


def test_matching_never_switches_route() -> None:
    expected = make_candidate(arrival_iata="LED")
    other = make_candidate(arrival_iata="SVO")
    matched = match_tracked_instance(
        [other, expected],
        provider_flight_id=None,
        flight_iata="FV6106",
        flight_date="2026-08-23",
        departure_iata="GOJ",
        arrival_iata="LED",
        identity_scheduled_local=expected.scheduled_departure.local_iso,
    )
    assert matched is expected


def test_matching_uses_local_wall_clock_for_same_route() -> None:
    closest = make_candidate(scheduled="2026-08-23T13:15:00+03:00")
    later = make_candidate(scheduled="2026-08-23T16:00:00+03:00")

    matched = match_tracked_instance(
        [later, closest],
        provider_flight_id=None,
        flight_iata="FV6106",
        flight_date="2026-08-23",
        departure_iata="GOJ",
        arrival_iata="LED",
        identity_scheduled_local="2026-08-23T13:20:00",
    )

    assert matched is closest


def test_matching_prefers_flightaware_id_over_similar_route() -> None:
    expected = make_candidate()
    expected.provider_flight_id = "expected-fa-id"
    replacement = make_candidate()
    replacement.provider_flight_id = "replacement-fa-id"

    matched = match_tracked_instance(
        [replacement, expected],
        provider_flight_id="expected-fa-id",
        flight_iata="FV6106",
        flight_date="2026-08-23",
        departure_iata="GOJ",
        arrival_iata="LED",
        identity_scheduled_local=expected.scheduled_departure.local_iso,
    )

    assert matched is expected
