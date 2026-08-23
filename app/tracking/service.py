from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.aviationstack.client import AviationstackClient
from app.aviationstack.errors import AviationstackError, QuotaExceededError
from app.aviationstack.matching import match_tracked_instance
from app.aviationstack.normalize import (
    candidate_to_state,
    normalize_future_response,
    normalize_realtime_response,
)
from app.aviationstack.selection import select_flight_candidates
from app.config import Settings
from app.domain.models import FlightCandidate, SubscriptionResult
from app.notifications.formatter import format_change_message, format_subscription_snapshot
from app.storage.db import Database
from app.tracking.diff import Change, diff_flight_state, preserve_transient_nulls
from app.tracking.policy import calculate_error_backoff, calculate_next_check
from app.tracking.quota import QuotaManager

LOGGER = logging.getLogger(__name__)


class TrackingService:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        client: AviationstackClient,
        quota: QuotaManager,
    ) -> None:
        self.settings = settings
        self.db = db
        self.client = client
        self.quota = quota

    def local_today(self) -> date:
        return datetime.now(ZoneInfo(self.settings.bot_default_timezone)).date()

    async def search_and_subscribe(
        self,
        *,
        user_id: int,
        chat_id: int,
        flight_iata: str,
        flight_date: str | None,
        departure_iata: str | None,
    ) -> SubscriptionResult:
        existing = await self.db.find_user_matching_subscriptions(
            user_id=user_id,
            chat_id=chat_id,
            flight_iata=flight_iata,
            flight_date=flight_date,
            departure_iata=departure_iata,
        )
        if len(existing) == 1:
            candidate = FlightCandidate.from_dict(
                json.loads(str(existing[0]["latest_candidate_json"]))
            )
            subscription_id = int(existing[0]["subscription_id"])
            tracking_enabled = not str(existing[0]["tracking_state"]).startswith("finished_")
            return SubscriptionResult(
                status="already_subscribed",
                message=format_subscription_snapshot(
                    candidate,
                    subscription_id=subscription_id,
                    tracking_enabled=tracking_enabled,
                ),
                candidate=candidate,
                subscription_id=subscription_id,
            )
        limit_result = await self._check_user_limit(user_id)
        if limit_result:
            return limit_result
        now = int(time.time())
        cached = await self.db.find_fresh_candidates(
            flight_iata=flight_iata,
            flight_date=flight_date,
            departure_iata=departure_iata,
            fresh_after_epoch=now - self.settings.search_cache_ttl_seconds,
        )
        selected_cached = select_flight_candidates(
            cached,
            requested_flight_iata=flight_iata,
            requested_date=flight_date,
        )
        if len(selected_cached) == 1:
            return await self.subscribe_candidate(
                user_id=user_id, chat_id=chat_id, candidate=selected_cached[0]
            )
        if len(selected_cached) > 1:
            return SubscriptionResult(
                status="ambiguous", message="Выберите конкретный рейс.", candidates=selected_cached
            )

        estimate = self._forecast_requests(flight_date)
        if not await self.quota.can_admit_forecast(estimate):
            return SubscriptionResult(
                status="quota",
                message="Недостаточно доступной квоты для нового отслеживания.",
            )
        try:
            far_future = bool(
                flight_date
                and date.fromisoformat(flight_date) > self.local_today() + timedelta(days=7)
            )
            if far_future:
                if not departure_iata:
                    return SubscriptionResult(
                        status="needs_departure",
                        message=(
                            "Для рейса более чем через семь дней укажите аэропорт отправления: "
                            f"/flight {flight_iata} {flight_date} GOJ"
                        ),
                    )
                payload = await self.client.search_future(
                    flight_iata,
                    flight_date=flight_date,
                    departure_iata=departure_iata,
                    flight_id=None,
                    trigger_type="user_search_future",
                    priority=10,
                )
                candidates = normalize_future_response(
                    payload,
                    requested_flight_iata=flight_iata,
                    requested_date=flight_date,
                )
            else:
                payload = await self.client.search_flights(
                    flight_iata,
                    flight_date=flight_date,
                    flight_id=None,
                    trigger_type="user_search",
                    priority=10,
                )
                candidates = normalize_realtime_response(
                    payload,
                    requested_flight_iata=flight_iata,
                    time_mode=self.settings.aviationstack_time_mode,
                )
        except QuotaExceededError:
            return SubscriptionResult(
                status="quota", message="Лимит запросов Aviationstack временно исчерпан."
            )
        except AviationstackError as exc:
            LOGGER.warning(
                "Initial flight search failed",
                extra={"event": "initial_search_failed", "api_error_code": exc.code},
            )
            if exc.code == "function_access_restricted":
                message = "Текущий тариф Aviationstack не поддерживает такой поиск."
            elif exc.code in {"provider_circuit_open", "invalid_access_key", "inactive_user"}:
                message = "Источник данных временно недоступен из-за ошибки конфигурации."
            else:
                message = "Не удалось получить данные о рейсе. Попробуйте позже."
            return SubscriptionResult(status="error", message=message)

        selected = select_flight_candidates(
            candidates,
            requested_flight_iata=flight_iata,
            requested_date=flight_date,
        )
        if departure_iata:
            selected = [item for item in selected if item.departure_iata == departure_iata]
        if not selected:
            return SubscriptionResult(
                status="not_found",
                message="Подходящий рейс не найден. Проверьте номер, дату и аэропорт.",
            )
        if len(selected) > 1:
            return SubscriptionResult(
                status="ambiguous",
                message="Найдено несколько рейсов. Выберите маршрут.",
                candidates=selected,
            )
        return await self.subscribe_candidate(
            user_id=user_id, chat_id=chat_id, candidate=selected[0]
        )

    async def subscribe_candidate(
        self, *, user_id: int, chat_id: int, candidate: FlightCandidate
    ) -> SubscriptionResult:
        existing_flight_id = await self.db.find_flight_id_by_candidate(candidate)
        if existing_flight_id is not None:
            existing_subscription = await self.db.find_active_subscription(
                existing_flight_id, user_id, chat_id
            )
            if existing_subscription is not None:
                row = await self.db.get_flight(existing_flight_id)
                tracking_enabled = bool(
                    row and not str(row["tracking_state"]).startswith("finished_")
                )
                return SubscriptionResult(
                    status="already_subscribed",
                    message=format_subscription_snapshot(
                        candidate,
                        subscription_id=existing_subscription,
                        tracking_enabled=tracking_enabled,
                    ),
                    candidate=candidate,
                    subscription_id=existing_subscription,
                )
        elif await self.db.count_active_flights() >= self.settings.max_active_tracked_flights:
            return SubscriptionResult(
                status="limit", message="Бот временно не принимает новые физические рейсы."
            )
        limit_result = await self._check_user_limit(user_id)
        if limit_result:
            return limit_result
        flight_id, _ = await self.db.create_or_get_flight(candidate)
        subscription_id, created = await self.db.add_subscription(flight_id, user_id, chat_id)
        row = await self.db.get_flight(flight_id)
        tracking_enabled = bool(row and not str(row["tracking_state"]).startswith("finished_"))
        if tracking_enabled:
            state = candidate_to_state(candidate, fetched_at_epoch=int(time.time()))
            reserve_only = await self.quota.reserve_only()
            decision = calculate_next_check(
                flight_id=flight_id,
                state=state,
                tracking_state=str(row["tracking_state"]),
                now_epoch=int(time.time()),
                reserve_only=reserve_only,
            )
            await self.db.set_flight_schedule(
                flight_id, decision.next_check_at_epoch, decision.priority
            )
        message = format_subscription_snapshot(
            candidate,
            subscription_id=subscription_id,
            tracking_enabled=tracking_enabled,
        )
        return SubscriptionResult(
            status="subscribed" if created else "already_subscribed",
            message=message,
            candidate=candidate,
            subscription_id=subscription_id,
        )

    async def process_poll(self, row: object, *, owner: str) -> None:
        flight = dict(row)
        flight_id = int(flight["id"])
        now = int(time.time())
        try:
            requested_date = str(flight["flight_date"])
            far_future = str(flight["tracking_state"]) == "future_scheduled" and date.fromisoformat(
                requested_date
            ) > self.local_today() + timedelta(days=7)
            if far_future:
                payload = await self.client.search_future(
                    str(flight["requested_flight_iata"]),
                    flight_date=requested_date,
                    departure_iata=str(flight["identity_departure_iata"]),
                    flight_id=flight_id,
                    trigger_type="scheduler_future",
                    priority=int(flight["polling_priority"]),
                )
                candidates = normalize_future_response(
                    payload,
                    requested_flight_iata=str(flight["requested_flight_iata"]),
                    requested_date=requested_date,
                )
            else:
                payload = await self.client.search_flights(
                    str(flight["requested_flight_iata"]),
                    flight_date=requested_date,
                    flight_id=flight_id,
                    trigger_type="scheduler",
                    priority=int(flight["polling_priority"]),
                )
                candidates = normalize_realtime_response(
                    payload,
                    requested_flight_iata=str(flight["requested_flight_iata"]),
                    time_mode=self.settings.aviationstack_time_mode,
                )
            candidate = match_tracked_instance(
                candidates,
                flight_iata=str(flight["provider_flight_iata"]),
                flight_date=requested_date,
                departure_iata=str(flight["identity_departure_iata"]),
                arrival_iata=str(flight["identity_arrival_iata"]),
                identity_scheduled_local=flight["identity_scheduled_departure_local"],
            )
            if candidate is None:
                failures = int(flight["consecutive_not_found"]) + 1
                backoff = calculate_error_backoff(now_epoch=now, consecutive_failures=failures)
                await self.db.mark_poll_failure(
                    flight_id,
                    owner=owner,
                    next_check_at_epoch=backoff.next_check_at_epoch,
                    not_found=True,
                )
                return
            old_state = json.loads(str(flight["normalized_state_json"]))
            new_state = preserve_transient_nulls(
                old_state, candidate_to_state(candidate, fetched_at_epoch=now)
            )
            changes = diff_flight_state(
                old_state,
                new_state,
                time_threshold_minutes=self.settings.notification_time_change_threshold_minutes,
            )
            tracking_state, finished_reason, landed_seen = self._next_tracking_state(
                flight, candidate, now
            )
            stale = self._is_stale(candidate, now)
            if stale and not tracking_state.startswith("finished_"):
                tracking_state = "finished_stale"
                finished_reason = "stale_after_arrival"
                if not changes:
                    changes = [Change("tracking_finished", None, "stale")]
            reserve_only = await self.quota.reserve_only()
            decision = calculate_next_check(
                flight_id=flight_id,
                state=new_state,
                tracking_state=tracking_state,
                now_epoch=now,
                reserve_only=reserve_only,
            )
            event_text = (
                format_change_message(
                    candidate,
                    changes,
                    tracking_state=tracking_state,
                    finished_reason=finished_reason,
                )
                if changes
                else None
            )
            event_kind = self._event_kind(changes, finished_reason)
            await self.db.apply_poll_success(
                flight_id=flight_id,
                owner=owner,
                candidate=candidate,
                normalized_state=new_state,
                tracking_state=tracking_state,
                next_check_at_epoch=decision.next_check_at_epoch,
                polling_priority=decision.priority,
                event_kind=event_kind if event_text else None,
                event_text=event_text,
                finished_reason=finished_reason,
                landed_seen_at_epoch=landed_seen,
            )
        except QuotaExceededError:
            await self.db.suspend_flight(flight_id, owner=owner, state="suspended_quota")
        except AviationstackError as exc:
            if exc.code in {
                "provider_circuit_open",
                "invalid_access_key",
                "missing_access_key",
                "inactive_user",
            }:
                await self.db.suspend_flight(flight_id, owner=owner, state="suspended_provider")
                return
            failures = int(flight["consecutive_failures"]) + 1
            backoff = calculate_error_backoff(
                now_epoch=now,
                consecutive_failures=failures,
                retry_after=exc.retry_after,
            )
            await self.db.mark_poll_failure(
                flight_id,
                owner=owner,
                next_check_at_epoch=backoff.next_check_at_epoch,
                not_found=False,
            )
            LOGGER.warning(
                "Scheduled flight check failed",
                extra={
                    "event": "poll_failed",
                    "flight_id": flight_id,
                    "api_error_code": exc.code,
                    "http_status": exc.http_status,
                },
            )

    async def _check_user_limit(self, user_id: int) -> SubscriptionResult | None:
        if (
            await self.db.count_active_subscriptions(user_id)
            >= self.settings.max_active_subscriptions_per_user
        ):
            return SubscriptionResult(
                status="limit",
                message="Достигнут лимит активных подписок. Остановите ненужные через /flights.",
            )
        return None

    def _forecast_requests(self, flight_date: str | None) -> int:
        if not flight_date:
            return 80
        days = max(0, (date.fromisoformat(flight_date) - self.local_today()).days)
        return 80 + min(days, 30) * (1 if days > 7 else 4)

    def _next_tracking_state(
        self, flight: dict, candidate: FlightCandidate, now: int
    ) -> tuple[str, str | None, int | None]:
        status = candidate.api_status
        if status == "cancelled":
            return "finished_cancelled", "cancelled", None
        if status == "landed":
            landed_seen = int(flight["landed_seen_at_epoch"] or now)
            grace = self.settings.post_landing_baggage_grace_minutes * 60
            if candidate.arrival_baggage or now >= landed_seen + grace:
                return "finished_landed", "landed", landed_seen
            return "post_landing", None, landed_seen
        if status == "active":
            return "active", None, None
        if status == "incident":
            return "incident", None, None
        if status == "diverted":
            return "diverted", None, None
        if candidate.source_kind == "future":
            return "future_scheduled", None, None
        return "scheduled", None, None

    def _is_stale(self, candidate: FlightCandidate, now: int) -> bool:
        arrival = candidate.effective_arrival_epoch
        if arrival is not None:
            return now > arrival + self.settings.arrival_stale_grace_hours * 3600
        try:
            created_day = date.fromisoformat(candidate.flight_date)
        except ValueError:
            return False
        return self.local_today() > created_day + timedelta(
            hours=self.settings.max_tracking_age_hours
        )

    @staticmethod
    def _event_kind(changes: list[Change], finished_reason: str | None) -> str:
        if finished_reason:
            return f"finished_{finished_reason}"
        statuses = [change.new for change in changes if change.field == "api_status"]
        return f"status_{statuses[-1]}" if statuses else "flight_changed"
