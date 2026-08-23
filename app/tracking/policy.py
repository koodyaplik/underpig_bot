from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain.models import PollDecision


def _nested_epoch(state: dict, section: str, field: str) -> int | None:
    value = state.get(section, {}).get(field)
    if isinstance(value, dict) and isinstance(value.get("utc_epoch"), int):
        return value["utc_epoch"]
    return None


def _jitter(interval: int, flight_id: int, *, critical: bool) -> int:
    digest = hashlib.sha256(f"{flight_id}:{interval}".encode()).digest()
    ratio = int.from_bytes(digest[:2], "big") / 65535
    maximum = min(int(interval * 0.05), 30 if critical else max(1, int(interval * 0.05)))
    return int(ratio * maximum)


def calculate_next_check(
    *,
    flight_id: int,
    state: dict,
    tracking_state: str,
    now_epoch: int,
    reserve_only: bool = False,
) -> PollDecision:
    status = state.get("api_status")
    departure = _nested_epoch(state, "departure", "estimated") or _nested_epoch(
        state, "departure", "scheduled"
    )
    arrival = _nested_epoch(state, "arrival", "estimated") or _nested_epoch(
        state, "arrival", "scheduled"
    )

    if tracking_state == "future_scheduled":
        interval, priority, reason = 24 * 3600, 20, "future_more_than_seven_days"
    elif status in {"active", "incident", "diverted"} or tracking_state in {
        "active",
        "incident",
        "diverted",
    }:
        remaining = arrival - now_epoch if arrival is not None else None
        if remaining is not None and remaining <= 30 * 60:
            interval, priority, reason = 5 * 60, 100, "active_near_arrival"
        else:
            interval, priority, reason = 10 * 60, 95, "active"
    elif tracking_state == "post_landing":
        interval, priority, reason = 10 * 60, 80, "post_landing_baggage"
    elif departure is None:
        interval, priority, reason = 30 * 60, 40, "missing_departure_time"
    else:
        remaining = departure - now_epoch
        if remaining > 7 * 86400:
            interval, priority, reason = 24 * 3600, 20, "more_than_seven_days"
        elif remaining > 24 * 3600:
            interval, priority, reason = 6 * 3600, 30, "one_to_seven_days"
        elif remaining > 12 * 3600:
            interval, priority, reason = 3 * 3600, 40, "twelve_to_twenty_four_hours"
        elif remaining > 6 * 3600:
            interval, priority, reason = 3600, 50, "six_to_twelve_hours"
        elif remaining > 2 * 3600:
            interval, priority, reason = 30 * 60, 60, "two_to_six_hours"
        elif remaining > 30 * 60:
            interval, priority, reason = 10 * 60, 75, "thirty_minutes_to_two_hours"
        else:
            interval, priority, reason = 5 * 60, 90, "less_than_thirty_minutes"

    critical = priority >= 80
    next_epoch = now_epoch + interval + _jitter(interval, flight_id, critical=critical)
    return PollDecision(
        next_check_at_epoch=next_epoch,
        reason=reason,
        priority=priority,
        uses_reserve=reserve_only and priority >= 80,
    )


@dataclass(slots=True, frozen=True)
class BackoffDecision:
    next_check_at_epoch: int
    delay_seconds: int


def calculate_error_backoff(
    *, now_epoch: int, consecutive_failures: int, retry_after: int | None = None
) -> BackoffDecision:
    sequence = (5 * 60, 15 * 60, 30 * 60, 60 * 60)
    delay = sequence[min(max(consecutive_failures - 1, 0), len(sequence) - 1)]
    if retry_after is not None:
        delay = max(delay, retry_after)
    return BackoffDecision(next_check_at_epoch=now_epoch + delay, delay_seconds=delay)
