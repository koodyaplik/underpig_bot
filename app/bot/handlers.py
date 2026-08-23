from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    candidate_keyboard,
    delete_confirmation_keyboard,
    subscription_keyboard,
    subscriptions_keyboard,
)
from app.bot.validators import CommandValidationError, parse_flight_command
from app.config import Settings
from app.storage.db import Database
from app.tracking.diff import STATUS_RU
from app.tracking.service import TrackingService

HELP_TEXT = """<b>Underpig Bot</b>

Пришлите голосовое сообщение — бот распознает его и вернет текст.

<b>Отслеживание авиарейсов</b>

Добавить рейс:
<code>/flight FV6106</code>

Бот всегда ищет рейс на сегодня. Статусы будут приходить в тот чат, где добавлен рейс.

Мои подписки: /flights
Остановить подписку: /stop

Обычный текст с номером рейса не запускает поиск."""

FLIGHT_COMMAND_CHAT_TYPES = {ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}


class CommandRateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.entries: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self.entries[user_id]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


def build_router(*, settings: Settings, db: Database, tracking: TrackingService) -> Router:
    router = Router(name="flight_bot")
    limiter = CommandRateLimiter(settings.flight_commands_per_user_per_minute)
    text_hints: dict[int, float] = {}

    def user_allowed(user_id: int) -> bool:
        allowed = settings.telegram_allowed_user_ids
        return not allowed or user_id in allowed

    async def command_allowed(message: Message) -> bool:
        if message.chat.type not in FLIGHT_COMMAND_CHAT_TYPES:
            await message.answer("Команды отслеживания доступны в личных чатах и группах.")
            return False
        if not message.from_user or not user_allowed(message.from_user.id):
            await message.answer("Доступ к этому боту ограничен.")
            return False
        return True

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if await command_allowed(message):
            await message.answer(HELP_TEXT, parse_mode="HTML")

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if await command_allowed(message):
            await message.answer(HELP_TEXT, parse_mode="HTML")

    @router.message(Command("flight"))
    async def flight_command(message: Message, command: CommandObject) -> None:
        if not await command_allowed(message) or not message.from_user:
            return
        if not limiter.allow(message.from_user.id):
            await message.answer("Слишком много запросов. Подождите минуту.")
            return
        try:
            parsed = parse_flight_command(command.args)
        except CommandValidationError as exc:
            await message.answer(escape(str(exc)), parse_mode="HTML")
            return
        await run_search(
            message,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            flight_iata=parsed.flight_iata,
            flight_date=datetime.now(ZoneInfo(settings.bot_default_timezone)).date().isoformat(),
            departure_iata=None,
        )

    async def run_search(
        message: Message,
        *,
        user_id: int,
        chat_id: int,
        flight_iata: str,
        flight_date: str | None,
        departure_iata: str | None,
    ) -> None:
        progress = await message.answer("Ищу рейс…")
        result = await tracking.search_and_subscribe(
            user_id=user_id,
            chat_id=chat_id,
            flight_iata=flight_iata,
            flight_date=flight_date,
            departure_iata=str(departure_iata) if departure_iata else None,
        )
        if result.status == "ambiguous" and result.candidates:
            pending = await db.create_pending_candidates(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                candidates=result.candidates,
                ttl_minutes=settings.pending_selection_ttl_minutes,
            )
            await progress.edit_text(
                "Найдено несколько вариантов. Выберите нужный рейс:",
                reply_markup=candidate_keyboard(pending),
            )
            return
        markup = (
            subscription_keyboard(result.subscription_id)
            if result.subscription_id and result.status in {"subscribed", "already_subscribed"}
            else None
        )
        await progress.edit_text(result.message, parse_mode="HTML", reply_markup=markup)

    @router.callback_query(F.data.startswith("fc:"))
    async def candidate_callback(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        token = callback.data.split(":", 1)[1]
        candidate = await db.consume_pending_candidate(token, callback.from_user.id)
        if not candidate:
            await callback.answer("Вариант устарел. Повторите /flight.", show_alert=True)
            return
        result = await tracking.subscribe_candidate(
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            candidate=candidate,
        )
        markup = subscription_keyboard(result.subscription_id) if result.subscription_id else None
        await callback.message.edit_text(result.message, parse_mode="HTML", reply_markup=markup)
        await callback.answer("Готово")

    @router.message(Command("flights"))
    async def flights_command(message: Message) -> None:
        if not await command_allowed(message) or not message.from_user:
            return
        rows = await db.list_user_subscriptions(message.from_user.id, message.chat.id)
        if not rows:
            await message.answer(
                "У вас нет активных подписок в этом чате. Добавьте рейс через /flight."
            )
            return
        lines = ["<b>Мои рейсы</b>", ""]
        ids: list[int] = []
        for row in rows:
            subscription_id = int(row["subscription_id"])
            ids.append(subscription_id)
            status = STATUS_RU.get(row["api_status"], row["api_status"] or STATUS_RU[None])
            lines.extend(
                (
                    f"<b>№{subscription_id} · {escape(str(row['requested_flight_iata']))}</b>",
                    f"{escape(str(row['departure_iata']))} → {escape(str(row['arrival_iata']))} · "
                    f"{escape(str(row['flight_date']))}",
                    f"Статус: {escape(str(status))}",
                    "",
                )
            )
        await message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=subscriptions_keyboard(ids),
        )

    @router.message(Command("stop"))
    async def stop_command(message: Message, command: CommandObject) -> None:
        if not await command_allowed(message) or not message.from_user:
            return
        argument = (command.args or "").strip()
        if not argument:
            rows = await db.list_user_subscriptions(message.from_user.id, message.chat.id)
            ids = [int(row["subscription_id"]) for row in rows]
            if not ids:
                await message.answer("У вас нет активных подписок в этом чате.")
            else:
                await message.answer(
                    "Выберите подписку, которую нужно остановить:",
                    reply_markup=subscriptions_keyboard(ids),
                )
            return
        if not argument.isdigit():
            await message.answer("Используйте номер подписки: /stop 123")
            return
        stopped = await db.stop_subscription(
            int(argument), message.from_user.id, chat_id=message.chat.id
        )
        await message.answer(
            "Отслеживание остановлено."
            if stopped
            else "Активная подписка с таким номером не найдена."
        )

    @router.callback_query(F.data.startswith("fs:"))
    async def stop_callback(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        value = callback.data.split(":", 1)[1]
        stopped = value.isdigit() and await db.stop_subscription(
            int(value), callback.from_user.id, chat_id=callback.message.chat.id
        )
        await callback.answer(
            "Отслеживание остановлено" if stopped else "Подписка не найдена",
            show_alert=not stopped,
        )
        if stopped:
            await callback.message.edit_reply_markup(reply_markup=None)

    @router.message(Command("delete_me"))
    async def delete_me_command(message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("Удаление персональных данных доступно только в личном чате.")
            return
        if await command_allowed(message):
            await message.answer(
                "Удалить ваши подписки и связанные Telegram-идентификаторы?",
                reply_markup=delete_confirmation_keyboard(),
            )

    @router.callback_query(F.data.startswith("delete_me:"))
    async def delete_me_callback(callback: CallbackQuery) -> None:
        if not callback.from_user or not callback.data or not callback.message:
            return
        if callback.data.endswith(":yes"):
            await db.delete_user(callback.from_user.id)
            await callback.message.edit_text("Ваши подписки и идентификаторы удалены.")
        else:
            await callback.message.edit_text("Удаление отменено.")
        await callback.answer()

    @router.message(F.text)
    async def ordinary_text(message: Message) -> None:
        if message.chat.type != ChatType.PRIVATE or not message.from_user:
            return
        now = time.monotonic()
        if text_hints.get(message.from_user.id, 0) > now - 30:
            return
        text_hints[message.from_user.id] = now
        await message.answer("Чтобы найти рейс, используйте явную команду: /flight FV6106")

    return router
