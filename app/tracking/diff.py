from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STATUS_RU = {
    "scheduled": "запланирован",
    "active": "в полете",
    "landed": "прибыл",
    "cancelled": "отменен",
    "incident": "инцидент",
    "diverted": "направлен в другой аэропорт",
    None: "нет подтвержденного статуса",
}


@dataclass(slots=True, frozen=True)
class Change:
    field: str
    old: Any
    new: Any


def _get(state: dict, section: str, field: str) -> Any:
    return state.get(section, {}).get(field)


def _epoch(value: object) -> int | None:
    return value.get("utc_epoch") if isinstance(value, dict) else None


def diff_flight_state(old: dict | None, new: dict, *, time_threshold_minutes: int) -> list[Change]:
    if not old:
        return []
    changes: list[Change] = []
    old_status, new_status = old.get("api_status"), new.get("api_status")
    if old_status != new_status and new_status is not None:
        changes.append(Change("api_status", old_status, new_status))

    for section in ("departure", "arrival"):
        for field in ("estimated", "actual"):
            old_value, new_value = _get(old, section, field), _get(new, section, field)
            if old_value == new_value or new_value is None:
                continue
            if (
                field == "estimated"
                and _epoch(old_value) is not None
                and _epoch(new_value) is not None
            ):
                delta = abs(_epoch(new_value) - _epoch(old_value))
                if delta < time_threshold_minutes * 60:
                    continue
            changes.append(Change(f"{section}.{field}", old_value, new_value))
        for field in ("delay_minutes", "terminal", "gate"):
            old_value, new_value = _get(old, section, field), _get(new, section, field)
            if old_value == new_value or new_value is None:
                continue
            if field == "delay_minutes" and old_value is not None:
                if abs(int(new_value) - int(old_value)) < time_threshold_minutes:
                    continue
            changes.append(Change(f"{section}.{field}", old_value, new_value))
    old_baggage = _get(old, "arrival", "baggage")
    new_baggage = _get(new, "arrival", "baggage")
    if new_baggage is not None and old_baggage != new_baggage:
        changes.append(Change("arrival.baggage", old_baggage, new_baggage))
    old_aircraft = old.get("aircraft_registration")
    new_aircraft = new.get("aircraft_registration")
    if old_aircraft is None and new_aircraft:
        changes.append(Change("aircraft_registration", old_aircraft, new_aircraft))
    return changes


def preserve_transient_nulls(old: dict | None, new: dict) -> dict:
    if not old:
        return new
    merged = {
        **new,
        "departure": dict(new.get("departure", {})),
        "arrival": dict(new.get("arrival", {})),
    }
    for section in ("departure", "arrival"):
        old_section = old.get(section, {})
        for field in (
            "scheduled",
            "estimated",
            "actual",
            "delay_minutes",
            "terminal",
            "gate",
            "baggage",
        ):
            if merged[section].get(field) is None and old_section.get(field) is not None:
                merged[section][field] = old_section[field]
    if merged.get("aircraft_registration") is None and old.get("aircraft_registration") is not None:
        merged["aircraft_registration"] = old["aircraft_registration"]
    return merged
