from __future__ import annotations

from app.aeroapi.normalize import normalize_flights_response, normalize_schedule_response


def test_live_flight_is_normalized_from_aeroapi_fields() -> None:
    payload = {
        "flights": [
            {
                "ident": "SDM6106",
                "ident_iata": "FV6106",
                "fa_flight_id": "SDM6106-1787480000-airline-0010",
                "operator": "Rossiya",
                "origin": {
                    "code_iata": "GOJ",
                    "code_icao": "UWGG",
                    "timezone": "Europe/Moscow",
                    "name": "Nizhny Novgorod",
                },
                "destination": {
                    "code_iata": "LED",
                    "code_icao": "ULLI",
                    "timezone": "Europe/Moscow",
                    "name": "Pulkovo",
                },
                "scheduled_out": "2026-08-23T10:15:00Z",
                "estimated_out": "2026-08-23T10:45:00Z",
                "scheduled_in": "2026-08-23T12:10:00Z",
                "actual_off": "2026-08-23T10:56:00Z",
                "actual_on": "2026-08-23T12:01:00Z",
                "departure_delay": 1800,
                "gate_origin": "12",
                "status": "Scheduled / Delayed",
            }
        ]
    }

    candidates = normalize_flights_response(
        payload,
        requested_flight_iata="FV6106",
        requested_date="2026-08-23",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.provider_flight_id == "SDM6106-1787480000-airline-0010"
    assert candidate.scheduled_departure.local_iso == "2026-08-23T13:15:00+03:00"
    assert candidate.departure_delay == 30
    assert candidate.departure_gate == "12"
    assert candidate.provider_status == "Scheduled / Delayed"
    assert candidate.actual_takeoff.local_iso == "2026-08-23T13:56:00+03:00"
    assert candidate.actual_landing.local_iso == "2026-08-23T15:01:00+03:00"


def test_live_flight_keeps_source_status_when_arrival_confirmation_is_missing() -> None:
    payload = {
        "flights": [
            {
                "ident": "SDM6175",
                "ident_iata": "FV6175",
                "codeshares_iata": ["SU6175"],
                "fa_flight_id": "SDM6175-1787294809-airline-722p",
                "origin": {
                    "code_iata": "LED",
                    "timezone": "Europe/Moscow",
                },
                "destination": {
                    "code_iata": "VKO",
                    "timezone": "Europe/Moscow",
                },
                "scheduled_out": "2026-08-23T13:00:00Z",
                "actual_out": "2026-08-23T12:49:00Z",
                "scheduled_in": "2026-08-23T14:35:00Z",
                "estimated_in": "2026-08-23T14:20:00Z",
                "actual_on": None,
                "actual_in": None,
                "status": "Вырулив. / Посадка закончена",
            }
        ]
    }

    candidates = normalize_flights_response(
        payload,
        requested_flight_iata="SU6175",
        requested_date="2026-08-23",
    )

    assert len(candidates) == 1
    assert candidates[0].api_status == "active"
    assert candidates[0].provider_status == "Вырулив. / Посадка закончена"


def test_schedule_uses_airport_timezones_to_filter_local_date() -> None:
    payload = {
        "scheduled": [
            {
                "ident_iata": "FV6106",
                "fa_flight_id": "SDM6106-schedule",
                "origin_icao": "UWGG",
                "origin_iata": "GOJ",
                "destination_icao": "ULLI",
                "destination_iata": "LED",
                "scheduled_out": "2026-08-22T21:30:00Z",
                "scheduled_in": "2026-08-22T23:10:00Z",
            }
        ]
    }
    airport_metadata = {
        "UWGG": {
            "code_icao": "UWGG",
            "code_iata": "GOJ",
            "name": "Nizhny Novgorod",
            "timezone": "Europe/Moscow",
        },
        "ULLI": {
            "code_icao": "ULLI",
            "code_iata": "LED",
            "name": "Pulkovo",
            "timezone": "Europe/Moscow",
        },
    }

    candidates = normalize_schedule_response(
        payload,
        requested_flight_iata="FV6106",
        requested_date="2026-08-23",
        airport_metadata=airport_metadata,
    )

    assert len(candidates) == 1
    assert candidates[0].scheduled_departure.local_iso == "2026-08-23T00:30:00+03:00"
    assert candidates[0].source_kind == "schedule"
