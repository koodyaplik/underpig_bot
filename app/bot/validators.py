from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

FLIGHT_RE = re.compile(r"^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$")
AIRPORT_RE = re.compile(r"^[A-Z]{3}$")


class CommandValidationError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class FlightCommand:
    flight_iata: str
    flight_date: str | None
    departure_iata: str | None


def normalize_flight_iata(value: str) -> str:
    normalized = value.strip().upper().replace("-", "")
    if not FLIGHT_RE.fullmatch(normalized):
        raise CommandValidationError("Неверный номер рейса. Используйте формат вроде FV6106.")
    return normalized


def normalize_airport_iata(value: str) -> str:
    normalized = value.strip().upper()
    if not AIRPORT_RE.fullmatch(normalized):
        raise CommandValidationError(
            "Неверный аэропорт. Нужен трехбуквенный IATA-код, например GOJ."
        )
    return normalized


def normalize_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CommandValidationError("Дата должна иметь формат YYYY-MM-DD.") from exc


def parse_flight_command(args: str | None) -> FlightCommand:
    parts = (args or "").split()
    if not parts:
        raise CommandValidationError("Укажите номер рейса: /flight FV6106")
    if len(parts) > 3:
        raise CommandValidationError("Формат: /flight FV6106 [YYYY-MM-DD] [GOJ]")
    flight_iata = normalize_flight_iata(parts[0])
    flight_date: str | None = None
    departure_iata: str | None = None
    if len(parts) >= 2:
        if AIRPORT_RE.fullmatch(parts[1].upper()):
            departure_iata = normalize_airport_iata(parts[1])
        else:
            flight_date = normalize_date(parts[1])
    if len(parts) == 3:
        if flight_date is None:
            raise CommandValidationError("Если указан аэропорт без даты, третий аргумент не нужен.")
        departure_iata = normalize_airport_iata(parts[2])
    return FlightCommand(flight_iata, flight_date, departure_iata)
