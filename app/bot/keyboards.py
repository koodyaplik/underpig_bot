from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.models import FlightCandidate
from app.notifications.formatter import candidate_label


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
