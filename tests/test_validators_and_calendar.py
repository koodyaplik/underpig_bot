from __future__ import annotations

import pytest

from app.bot.validators import CommandValidationError, parse_flight_command


def test_explicit_command_parsing() -> None:
    parsed = parse_flight_command("fv6106")
    assert parsed.flight_iata == "FV6106"


@pytest.mark.parametrize("value", ["FV6106 2026-08-23", "FV6106 GOJ"])
def test_date_and_airport_arguments_are_rejected(value: str) -> None:
    with pytest.raises(CommandValidationError, match="всегда ищет рейс на сегодня"):
        parse_flight_command(value)


def test_missing_flight_number_is_rejected() -> None:
    with pytest.raises(CommandValidationError):
        parse_flight_command(None)
