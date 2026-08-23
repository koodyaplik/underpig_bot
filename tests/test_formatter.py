from __future__ import annotations

from conftest import make_candidate

from app.aeroapi.time_parser import parse_aeroapi_time
from app.notifications.formatter import format_change_message, format_subscription_snapshot
from app.tracking.diff import Change


def test_subscription_snapshot_uses_provider_status_verbatim() -> None:
    candidate = make_candidate(provider_status="Выл. / Прил. по расписанию")

    message = format_subscription_snapshot(
        candidate,
        subscription_id=1,
        tracking_enabled=True,
    )

    assert "Статус: Выл. / Прил. по расписанию" in message
    assert "Статус: запланирован" not in message


def test_status_notification_uses_current_provider_status() -> None:
    candidate = make_candidate(status="landed", provider_status="Прибыл")

    message = format_change_message(
        candidate,
        [Change("provider_status", "Выл. / Прил. по расписанию", "Прибыл")],
        tracking_state="post_landing",
        finished_reason=None,
    )

    assert "Статус: <b>Прибыл</b>" in message
    assert "Статус: <b>прибыл</b>" not in message


def test_actual_early_departure_is_written_without_negative_minutes() -> None:
    candidate = make_candidate()
    candidate.actual_departure = parse_aeroapi_time(
        "2026-08-23T13:04:00+00:00", candidate.departure_timezone
    )

    message = format_change_message(
        candidate,
        [
            Change("departure.actual", None, candidate.actual_departure.to_dict()),
            Change("departure.delay_minutes", None, -11),
        ],
        tracking_state="active",
        finished_reason=None,
    )

    assert "Вылетел раньше на 11 минут." in message
    assert "-11" not in message
    assert message.count("Вылетел раньше") == 1


def test_estimated_early_arrival_is_written_without_negative_minutes() -> None:
    candidate = make_candidate()
    candidate.estimated_arrival = parse_aeroapi_time(
        "2026-08-23T14:59:00+00:00", candidate.arrival_timezone
    )

    message = format_change_message(
        candidate,
        [Change("arrival.delay_minutes", None, -11)],
        tracking_state="active",
        finished_reason=None,
    )

    assert "Ожидается прилёт раньше на 11 минут." in message
    assert "-11" not in message


def test_late_arrival_uses_delay_wording_and_russian_plural() -> None:
    candidate = make_candidate()

    message = format_change_message(
        candidate,
        [Change("arrival.delay_minutes", None, 22)],
        tracking_state="active",
        finished_reason=None,
    )

    assert "Ожидается задержка прилёта на 22 минуты." in message


def test_takeoff_and_landing_notifications_use_runway_events() -> None:
    candidate = make_candidate(status="landed")
    candidate.actual_takeoff = parse_aeroapi_time(
        "2026-08-23T13:25:00+00:00", candidate.departure_timezone
    )
    candidate.actual_landing = parse_aeroapi_time(
        "2026-08-23T15:00:00+00:00", candidate.arrival_timezone
    )

    message = format_change_message(
        candidate,
        [
            Change("departure.takeoff_actual", None, candidate.actual_takeoff.to_dict()),
            Change("arrival.landing_actual", None, candidate.actual_landing.to_dict()),
        ],
        tracking_state="post_landing",
        finished_reason=None,
    )

    assert "Взлетел: <b>" in message
    assert "Приземлился: <b>" in message


def test_runway_departure_fallback_is_not_duplicated_as_gate_departure() -> None:
    candidate = make_candidate(status="active")
    candidate.actual_departure = parse_aeroapi_time(
        "2026-08-23T13:25:00+00:00", candidate.departure_timezone
    )
    candidate.actual_takeoff = candidate.actual_departure
    candidate.raw = {"actual_out": None, "actual_off": "2026-08-23T13:25:00+00:00"}

    message = format_change_message(
        candidate,
        [
            Change("departure.actual", None, candidate.actual_departure.to_dict()),
            Change("departure.takeoff_actual", None, candidate.actual_takeoff.to_dict()),
        ],
        tracking_state="active",
        finished_reason=None,
    )

    assert "Взлетел: <b>" in message
    assert "Покинул гейт" not in message


def test_gate_departure_does_not_claim_that_aircraft_took_off() -> None:
    candidate = make_candidate(status="active")
    candidate.actual_departure = parse_aeroapi_time(
        "2026-08-23T13:04:00+00:00", candidate.departure_timezone
    )
    candidate.raw = {"actual_out": "2026-08-23T13:04:00+00:00", "actual_off": None}

    message = format_change_message(
        candidate,
        [Change("departure.actual", None, candidate.actual_departure.to_dict())],
        tracking_state="active",
        finished_reason=None,
    )

    assert "Покинул гейт: <b>" in message
    assert "Взлетел:" not in message
