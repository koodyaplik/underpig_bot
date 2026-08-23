from __future__ import annotations

from datetime import date

import pytest

from app.bot.keyboards import calendar_keyboard, date_shortcuts
from app.bot.validators import CommandValidationError, parse_flight_command


def test_explicit_command_parsing() -> None:
    parsed = parse_flight_command("fv6106 2026-08-23 goj")
    assert parsed.flight_iata == "FV6106"
    assert parsed.flight_date == "2026-08-23"
    assert parsed.departure_iata == "GOJ"


def test_airport_can_be_given_before_calendar() -> None:
    parsed = parse_flight_command("FV6106 GOJ")
    assert parsed.flight_date is None
    assert parsed.departure_iata == "GOJ"


def test_missing_flight_number_is_rejected() -> None:
    with pytest.raises(CommandValidationError):
        parse_flight_command(None)


def test_calendar_callbacks_fit_telegram_limit() -> None:
    keyboards = [
        date_shortcuts("abcdefgh", date(2026, 8, 23)),
        calendar_keyboard("abcdefgh", 2026, 8),
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
