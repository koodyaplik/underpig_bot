from __future__ import annotations

from conftest import make_candidate

from app.aeroapi.normalize import candidate_to_state
from app.tracking.diff import (
    Change,
    backfill_provider_status,
    diff_flight_state,
    preserve_transient_nulls,
)
from app.tracking.policy import calculate_error_backoff, calculate_next_check


def test_small_estimated_change_is_suppressed() -> None:
    old = candidate_to_state(
        make_candidate(estimated="2026-08-23T13:15:00+00:00"), fetched_at_epoch=1
    )
    new = candidate_to_state(
        make_candidate(estimated="2026-08-23T13:17:00+00:00"), fetched_at_epoch=2
    )
    changes = diff_flight_state(old, new, time_threshold_minutes=5)
    assert not [change for change in changes if change.field == "departure.estimated"]


def test_gate_and_status_are_coalesced_as_changes() -> None:
    old = candidate_to_state(make_candidate(), fetched_at_epoch=1)
    new = candidate_to_state(make_candidate(status="active", gate="12"), fetched_at_epoch=2)
    fields = {change.field for change in diff_flight_state(old, new, time_threshold_minutes=5)}
    assert fields == {"api_status", "departure.gate"}


def test_provider_status_change_is_reported_verbatim() -> None:
    old = candidate_to_state(
        make_candidate(provider_status="Выл. / Прил. по расписанию"), fetched_at_epoch=1
    )
    new = candidate_to_state(
        make_candidate(status="landed", provider_status="Прибыл"), fetched_at_epoch=2
    )

    changes = diff_flight_state(old, new, time_threshold_minutes=5)

    assert Change("provider_status", "Выл. / Прил. по расписанию", "Прибыл") in changes
    assert not [change for change in changes if change.field == "api_status"]


def test_old_state_uses_saved_raw_status_to_detect_next_source_change() -> None:
    stored_candidate = make_candidate(provider_status="В полете / По расписанию")
    serialized = stored_candidate.to_dict()
    serialized["raw"]["status"] = stored_candidate.provider_status
    serialized.pop("provider_status")
    restored_candidate = type(stored_candidate).from_dict(serialized)
    old_state = candidate_to_state(stored_candidate, fetched_at_epoch=1)
    old_state.pop("provider_status")
    old_state = backfill_provider_status(old_state, restored_candidate.provider_status)
    new_state = candidate_to_state(
        make_candidate(provider_status="Вырулив. / Посадка закончена"), fetched_at_epoch=2
    )

    changes = diff_flight_state(old_state, new_state, time_threshold_minutes=5)

    assert changes == [
        Change(
            "provider_status",
            "В полете / По расписанию",
            "Вырулив. / Посадка закончена",
        )
    ]


def test_overdue_active_legacy_state_reports_current_source_status_once() -> None:
    old_state = candidate_to_state(
        make_candidate(provider_status="В полете / По расписанию"), fetched_at_epoch=1
    )
    old_state.pop("provider_status")
    old_state = backfill_provider_status(
        old_state,
        "В полете / По расписанию",
        notify_current=True,
    )
    new_state = candidate_to_state(
        make_candidate(provider_status="Вырулив. / Посадка закончена"), fetched_at_epoch=2
    )

    changes = diff_flight_state(old_state, new_state, time_threshold_minutes=5)

    assert changes == [Change("provider_status", None, "Вырулив. / Посадка закончена")]


def test_effective_delay_is_derived_from_estimated_time() -> None:
    state = candidate_to_state(
        make_candidate(estimated="2026-08-23T14:05:00+00:00"), fetched_at_epoch=1
    )
    assert state["departure"]["delay_minutes"] == 50


def test_transient_null_does_not_erase_known_gate() -> None:
    old = candidate_to_state(make_candidate(gate="12"), fetched_at_epoch=1)
    incomplete = candidate_to_state(make_candidate(gate=None), fetched_at_epoch=2)
    merged = preserve_transient_nulls(old, incomplete)
    assert merged["departure"]["gate"] == "12"


def test_policy_uses_five_minutes_near_departure() -> None:
    state = candidate_to_state(make_candidate(), fetched_at_epoch=1)
    departure = state["departure"]["scheduled"]["utc_epoch"]
    decision = calculate_next_check(
        flight_id=1,
        state=state,
        tracking_state="scheduled",
        now_epoch=departure - 20 * 60,
    )
    assert 300 <= decision.next_check_at_epoch - (departure - 20 * 60) <= 330
    assert decision.priority == 90


def test_backoff_is_capped() -> None:
    decision = calculate_error_backoff(now_epoch=1000, consecutive_failures=99)
    assert decision.delay_seconds == 3600
