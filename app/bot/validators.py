from __future__ import annotations

import re
from dataclasses import dataclass

FLIGHT_RE = re.compile(r"^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$")


class CommandValidationError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class FlightCommand:
    flight_iata: str


def normalize_flight_iata(value: str) -> str:
    normalized = value.strip().upper().replace("-", "")
    if not FLIGHT_RE.fullmatch(normalized):
        raise CommandValidationError("Неверный номер рейса. Используйте формат вроде FV6106.")
    return normalized


def parse_flight_command(args: str | None) -> FlightCommand:
    parts = (args or "").split()
    if not parts:
        raise CommandValidationError("Укажите номер рейса: /flight FV6106")
    if len(parts) != 1:
        raise CommandValidationError("Формат: /flight FV6106. Бот всегда ищет рейс на сегодня.")
    return FlightCommand(normalize_flight_iata(parts[0]))
