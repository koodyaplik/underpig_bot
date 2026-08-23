from __future__ import annotations

import calendar
from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.models import FlightCandidate
from app.notifications.formatter import candidate_label

MONTHS_RU = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def date_shortcuts(token: str, today: date) -> InlineKeyboardMarkup:
    tomorrow = today + timedelta(days=1)
    after = today + timedelta(days=2)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Сегодня · {today:%d.%m}", callback_data=f"fd:{token}:t0"
                ),
                InlineKeyboardButton(
                    text=f"Завтра · {tomorrow:%d.%m}", callback_data=f"fd:{token}:t1"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Послезавтра · {after:%d.%m}", callback_data=f"fd:{token}:t2"
                ),
                InlineKeyboardButton(text="Другая дата", callback_data=f"fd:{token}:cal"),
            ],
            [InlineKeyboardButton(text="Не знаю дату", callback_data=f"fd:{token}:any")],
            [InlineKeyboardButton(text="Отмена", callback_data=f"fd:{token}:cancel")],
        ]
    )


def calendar_keyboard(token: str, year: int, month: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"{MONTHS_RU[month]} {year}", callback_data=f"fd:{token}:noop")],
        [
            InlineKeyboardButton(text=day, callback_data=f"fd:{token}:noop")
            for day in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        ],
    ]
    month_calendar = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in month_calendar:
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(day) if day else " ",
                    callback_data=(
                        f"fd:{token}:d:{year:04d}{month:02d}{day:02d}"
                        if day
                        else f"fd:{token}:noop"
                    ),
                )
                for day in week
            ]
        )
    previous = date(year, month, 1) - timedelta(days=1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    rows.append(
        [
            InlineKeyboardButton(
                text="‹", callback_data=f"fd:{token}:m:{previous.year:04d}{previous.month:02d}"
            ),
            InlineKeyboardButton(text="Быстрый выбор", callback_data=f"fd:{token}:quick"),
            InlineKeyboardButton(
                text="›", callback_data=f"fd:{token}:m:{next_month.year:04d}{next_month.month:02d}"
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"fd:{token}:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def candidate_keyboard(items: list[tuple[str, FlightCandidate]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=candidate_label(candidate), callback_data=f"fc:{token}")]
            for token, candidate in items
        ]
    )


def subscription_keyboard(subscription_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Остановить отслеживание", callback_data=f"fs:{subscription_id}"
                )
            ]
        ]
    )


def subscriptions_keyboard(subscription_ids: list[int]) -> InlineKeyboardMarkup | None:
    if not subscription_ids:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Остановить №{item}", callback_data=f"fs:{item}")]
            for item in subscription_ids
        ]
    )


def delete_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Удалить мои данные", callback_data="delete_me:yes"),
                InlineKeyboardButton(text="Отмена", callback_data="delete_me:no"),
            ]
        ]
    )
