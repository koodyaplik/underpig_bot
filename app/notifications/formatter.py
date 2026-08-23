from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from app.domain.models import FlightCandidate, ParsedTime
from app.tracking.diff import STATUS_RU, Change


def _safe(value: object, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value))


def _format_parsed(value: ParsedTime | dict[str, Any] | None, *, include_date: bool = True) -> str:
    if isinstance(value, ParsedTime):
        local_iso, timezone = value.local_iso, value.timezone
    elif isinstance(value, dict):
        local_iso, timezone = value.get("local_iso"), value.get("timezone")
    else:
        return "—"
    if not local_iso:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(local_iso))
        pattern = "%d.%m.%Y %H:%M" if include_date else "%H:%M"
        rendered = parsed.strftime(pattern)
    except ValueError:
        rendered = str(local_iso)
    return f"{escape(rendered)} {escape(str(timezone))}" if timezone else escape(rendered)


def candidate_label(candidate: FlightCandidate) -> str:
    dep_time = _format_parsed(candidate.scheduled_departure, include_date=False)
    return (
        f"{candidate.departure_iata} → {candidate.arrival_iata} · "
        f"{candidate.flight_date} · {dep_time}"
    )[:60]


def display_status(provider_status: object, api_status: object) -> str:
    if provider_status is not None and str(provider_status).strip():
        return str(provider_status).strip()
    return STATUS_RU.get(api_status, api_status or STATUS_RU[None])


def format_subscription_snapshot(
    candidate: FlightCandidate,
    *,
    subscription_id: int,
    tracking_enabled: bool,
) -> str:
    dep_name = candidate.departure_airport or candidate.departure_iata
    arr_name = candidate.arrival_airport or candidate.arrival_iata
    status = display_status(candidate.provider_status, candidate.api_status)
    tail = (
        "Отслеживание включено."
        if tracking_enabled
        else "Рейс уже завершен. Автоматическое отслеживание не запущено."
    )
    return (
        f"✈️ <b>{_safe(candidate.requested_flight_iata)}</b>\n"
        f"{_safe(dep_name)} ({_safe(candidate.departure_iata)}) → "
        f"{_safe(arr_name)} ({_safe(candidate.arrival_iata)})\n"
        f"Дата рейса: {_safe(candidate.flight_date)}\n\n"
        f"Вылет: {_format_parsed(candidate.estimated_departure or candidate.scheduled_departure)}\n"
        f"Прилет: {_format_parsed(candidate.estimated_arrival or candidate.scheduled_arrival)}\n"
        f"Статус: {_safe(status)}\n\n"
        f"{tail}\n"
        f"Номер подписки: <code>{subscription_id}</code>"
    )


def format_change_message(
    candidate: FlightCandidate,
    changes: list[Change],
    *,
    tracking_state: str,
    finished_reason: str | None,
) -> str:
    lines = [
        f"✈️ <b>{_safe(candidate.requested_flight_iata)}</b> — обновление",
        f"{_safe(candidate.departure_iata)} → {_safe(candidate.arrival_iata)}",
        "",
    ]
    for change in changes:
        if change.field in {"api_status", "provider_status"}:
            status = display_status(candidate.provider_status, candidate.api_status)
            lines.append(f"Статус: <b>{_safe(status)}</b>")
        elif change.field.endswith(".estimated"):
            label = "Вылет" if change.field.startswith("departure") else "Прилет"
            lines.append(
                f"{label}: {_format_parsed(change.old)} → <b>{_format_parsed(change.new)}</b>"
            )
        elif change.field.endswith(".actual"):
            label = (
                "Фактический вылет"
                if change.field.startswith("departure")
                else "Фактическое прибытие"
            )
            lines.append(f"{label}: <b>{_format_parsed(change.new)}</b>")
        elif change.field.endswith(".delay_minutes"):
            label = (
                "Задержка вылета" if change.field.startswith("departure") else "Задержка прилета"
            )
            lines.append(f"{label}: <b>{_safe(change.new)} мин.</b>")
        elif change.field.endswith(".gate"):
            place = "отправления" if change.field.startswith("departure") else "прибытия"
            lines.append(f"Выход {place}: <b>{_safe(change.new)}</b>")
        elif change.field.endswith(".terminal"):
            place = "отправления" if change.field.startswith("departure") else "прибытия"
            lines.append(f"Терминал {place}: <b>{_safe(change.new)}</b>")
        elif change.field == "arrival.baggage":
            lines.append(f"Выдача багажа: <b>{_safe(change.new)}</b>")
        elif change.field == "aircraft_registration":
            lines.append(f"Борт: <b>{_safe(change.new)}</b>")
    if tracking_state == "post_landing":
        lines.extend(("", "Рейс прибыл. Еще некоторое время проверяю выдачу багажа."))
    elif tracking_state.startswith("finished_"):
        lines.extend(("", "Автоматическое отслеживание завершено."))
    if finished_reason == "stale_after_arrival":
        lines = [
            f"✈️ <b>{_safe(candidate.requested_flight_iata)}</b>",
            "",
            "Новых подтвержденных данных после расчетного времени прибытия нет.",
            "Автоматическое отслеживание завершено без подтверждения посадки.",
        ]
    return "\n".join(lines)
