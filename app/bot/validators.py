from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

FLIGHT_RE = re.compile(r"^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$")


class CommandValidationError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class FlightCommand:
    flight_iata: str
    flight_date: str | None


def normalize_flight_iata(value: str) -> str:
    normalized = value.strip().upper().replace("-", "")
    if not FLIGHT_RE.fullmatch(normalized):
        raise CommandValidationError("Неверный номер рейса. Используйте формат вроде FV6106.")
    return normalized


def parse_flight_command(args: str | None) -> FlightCommand:
    parts = (args or "").split()
    if not parts:
        raise CommandValidationError("Укажите номер рейса: /flight FV6106")
    if len(parts) > 2:
        raise CommandValidationError("Формат: /flight FV6106 [YYYY-MM-DD]")
    flight_date: str | None = None
    if len(parts) == 2:
        try:
            flight_date = date.fromisoformat(parts[1]).isoformat()
        except ValueError as exc:
            raise CommandValidationError("Дата должна иметь формат YYYY-MM-DD.") from exc
    return FlightCommand(normalize_flight_iata(parts[0]), flight_date)
