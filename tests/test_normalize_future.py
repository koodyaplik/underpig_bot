from __future__ import annotations

from app.aviationstack.normalize import normalize_future_response


def test_overnight_future_flight_arrives_next_day() -> None:
    payload = {
        "data": [
            [
                {
                    "airline": {"name": "Rossiya"},
                    "flight": {"iataNumber": "FV6106"},
                    "departure": {
                        "iataCode": "GOJ",
                        "scheduledTime": "23:10",
                    },
                    "arrival": {
                        "iataCode": "LED",
                        "scheduledTime": "01:05",
                    },
                }
            ]
        ]
    }

    candidates = normalize_future_response(
        payload,
        requested_flight_iata="FV6106",
        requested_date="2026-08-23",
    )

    assert len(candidates) == 1
    assert candidates[0].scheduled_departure.local_iso == "2026-08-23T23:10:00"
    assert candidates[0].scheduled_arrival.local_iso == "2026-08-24T01:05:00"
