from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.bot.keyboards import calendar_keyboard, date_shortcuts
from app.bot.validators import CommandValidationError, parse_flight_command


def test_explicit_command_parsing() -> None:
    parsed = parse_flight_command("fv6106 2026-08-23")
    assert parsed.flight_iata == "FV6106"
    assert parsed.flight_date == "2026-08-23"


def test_command_without_date_opens_calendar_flow() -> None:
    parsed = parse_flight_command("FV6106")
    assert parsed.flight_date is None


def test_invalid_date_is_rejected() -> None:
    with pytest.raises(CommandValidationError, match="YYYY-MM-DD"):
        parse_flight_command("FV6106 tomorrow")


def test_missing_flight_number_is_rejected() -> None:
    with pytest.raises(CommandValidationError):
        parse_flight_command(None)


def test_calendar_disables_dates_outside_provider_window() -> None:
    today = date(2026, 8, 23)
    keyboard = calendar_keyboard(
        "abcdefgh",
        2026,
        8,
        minimum=today,
        maximum=today + timedelta(days=363),
    )
    day_callbacks = {
        button.text: button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.text in {"22", "23", "24"}
    }
    assert day_callbacks["22"].endswith(":noop")
    assert day_callbacks["23"].endswith(":d:20260823")
    assert day_callbacks["24"].endswith(":d:20260824")


def test_calendar_callbacks_fit_telegram_limit() -> None:
    today = date(2026, 8, 23)
    keyboards = [
        date_shortcuts("abcdefgh", today),
        calendar_keyboard(
            "abcdefgh",
            2026,
            8,
            minimum=today,
            maximum=today + timedelta(days=363),
        ),
    ]
    callbacks = [
        button.callback_data
        for keyboard in keyboards
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
