from __future__ import annotations

from conftest import make_candidate

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
