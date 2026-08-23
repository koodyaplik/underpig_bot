from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

import aiosqlite

from app.aeroapi.normalize import candidate_to_state
from app.domain.models import FlightCandidate

POLLABLE_STATES = (
    "scheduled",
    "active",
    "incident",
    "diverted",
    "post_landing",
    "future_scheduled",
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _now() -> int:
    return int(time.time())


def _time_field(candidate: FlightCandidate, name: str, attribute: str) -> object:
    parsed = getattr(candidate, name)
    return getattr(parsed, attribute) if parsed else None


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not connected")
        return self.connection

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA foreign_keys = ON")
        await self.connection.execute("PRAGMA journal_mode = WAL")
        await self.connection.execute("PRAGMA busy_timeout = 5000")
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def migrate(self) -> None:
        sql_path = Path(__file__).parent / "sql" / "001_initial.sql"
        script = sql_path.read_text(encoding="utf-8")
        async with self._write_lock:
            await self.conn.executescript(script)
            await self.conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at_epoch) VALUES(1, ?)",
                (_now(),),
            )
            await self.conn.commit()

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(query, params)
        return await cursor.fetchone()

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(query, params)
        return list(await cursor.fetchall())

    async def get_service_state(self, key: str) -> str | None:
        row = await self.fetchone("SELECT value FROM service_state WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    async def set_service_state(self, key: str, value: str) -> None:
        now = _now()
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO service_state(key, value, updated_at_epoch) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_epoch=excluded.updated_at_epoch
                """,
                (key, value, now),
            )
            await self.conn.commit()

    async def heartbeat(self, worker: str) -> None:
        await self.set_service_state(f"heartbeat:{worker}", str(_now()))

    async def start_api_request(
        self,
        *,
        endpoint_name: str,
        flight_id: int | None,
        trigger_type: str,
        priority: int,
    ) -> int:
        now = _now()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO api_requests(
                    request_started_at_epoch, endpoint_name, flight_id, trigger_type,
                    priority, attempted_cost, created_at_epoch
                ) VALUES(?, ?, ?, ?, ?, 1, ?)
                """,
                (now, endpoint_name, flight_id, trigger_type, priority, now),
            )
            await self.conn.commit()
            return int(cursor.lastrowid)

    async def finish_api_request(
        self,
        request_id: int,
        *,
        success: bool,
        http_status: int | None,
        api_error_code: str | None,
        duration_ms: int,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE api_requests
                SET request_finished_at_epoch=?, success=?, http_status=?, api_error_code=?, duration_ms=?
                WHERE id=?
                """,
                (_now(), int(success), http_status, api_error_code, duration_ms, request_id),
            )
            await self.conn.commit()

    async def count_api_requests_since(self, start_epoch: int) -> int:
        row = await self.fetchone(
            "SELECT COALESCE(SUM(attempted_cost), 0) AS count FROM api_requests WHERE request_started_at_epoch >= ?",
            (start_epoch,),
        )
        return int(row["count"]) if row else 0

    async def create_date_session(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        flight_iata: str,
        departure_iata: str | None,
        ttl_minutes: int,
    ) -> str:
        token = secrets.token_urlsafe(6)
        now = _now()
        async with self._write_lock:
            await self.conn.execute(
                """
                DELETE FROM date_selection_sessions
                WHERE telegram_user_id=? AND telegram_chat_id=? AND used_at_epoch IS NULL
                """,
                (telegram_user_id, telegram_chat_id),
            )
            await self.conn.execute(
                """
                INSERT INTO date_selection_sessions(
                    token, telegram_user_id, telegram_chat_id, flight_iata,
                    departure_iata, expires_at_epoch, created_at_epoch
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    telegram_user_id,
                    telegram_chat_id,
                    flight_iata,
                    departure_iata,
                    now + ttl_minutes * 60,
                    now,
                ),
            )
            await self.conn.commit()
        return token

    async def get_date_session(self, token: str, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone(
            """
            SELECT * FROM date_selection_sessions
            WHERE token=? AND telegram_user_id=? AND used_at_epoch IS NULL AND expires_at_epoch>=?
            """,
            (token, user_id, _now()),
        )

    async def use_date_session(self, token: str) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE date_selection_sessions SET used_at_epoch=? WHERE token=? AND used_at_epoch IS NULL",
                (_now(), token),
            )
            await self.conn.commit()

    async def create_pending_candidates(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        candidates: list[FlightCandidate],
        ttl_minutes: int,
    ) -> list[tuple[str, FlightCandidate]]:
        now = _now()
        result: list[tuple[str, FlightCandidate]] = []
        async with self._write_lock:
            for candidate in candidates:
                token = secrets.token_urlsafe(6)
                await self.conn.execute(
                    """
                    INSERT INTO pending_selections(
                        token, telegram_user_id, telegram_chat_id, candidate_json,
                        expires_at_epoch, created_at_epoch
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token,
                        telegram_user_id,
                        telegram_chat_id,
                        _json(candidate.to_dict()),
                        now + ttl_minutes * 60,
                        now,
                    ),
                )
                result.append((token, candidate))
            await self.conn.commit()
        return result

    async def consume_pending_candidate(self, token: str, user_id: int) -> FlightCandidate | None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            cursor = await self.conn.execute(
                """
                SELECT candidate_json FROM pending_selections
                WHERE token=? AND telegram_user_id=? AND used_at_epoch IS NULL AND expires_at_epoch>=?
                """,
                (token, user_id, _now()),
            )
            row = await cursor.fetchone()
            if not row:
                await self.conn.rollback()
                return None
            await self.conn.execute(
                "UPDATE pending_selections SET used_at_epoch=? WHERE token=?",
                (_now(), token),
            )
            await self.conn.commit()
        return FlightCandidate.from_dict(json.loads(row["candidate_json"]))

    async def count_active_subscriptions(self, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS count FROM subscriptions WHERE telegram_user_id=? AND active=1",
            (user_id,),
        )
        return int(row["count"]) if row else 0

    async def count_active_flights(self) -> int:
        placeholders = ",".join("?" for _ in POLLABLE_STATES)
        row = await self.fetchone(
            f"SELECT COUNT(*) AS count FROM flights WHERE tracking_state IN ({placeholders})",
            POLLABLE_STATES,
        )
        return int(row["count"]) if row else 0

    async def find_flight_id_by_candidate(self, candidate: FlightCandidate) -> int | None:
        row = await self.fetchone(
            "SELECT id FROM flights WHERE instance_key=?", (self.instance_key(candidate),)
        )
        return int(row["id"]) if row else None

    async def find_active_subscription(
        self, flight_id: int, user_id: int, chat_id: int
    ) -> int | None:
        row = await self.fetchone(
            """
            SELECT id FROM subscriptions
            WHERE flight_id=? AND telegram_user_id=? AND telegram_chat_id=? AND active=1
            """,
            (flight_id, user_id, chat_id),
        )
        return int(row["id"]) if row else None

    async def find_user_matching_subscriptions(
        self,
        *,
        user_id: int,
        chat_id: int,
        flight_iata: str,
        flight_date: str | None,
        departure_iata: str | None,
    ) -> list[aiosqlite.Row]:
        clauses = [
            "s.telegram_user_id=?",
            "s.telegram_chat_id=?",
            "s.active=1",
            "f.requested_flight_iata=?",
        ]
        params: list[Any] = [user_id, chat_id, flight_iata]
        if flight_date:
            clauses.append("f.flight_date=?")
            params.append(flight_date)
        if departure_iata:
            clauses.append("f.departure_iata=?")
            params.append(departure_iata)
        return await self.fetchall(
            f"""
            SELECT s.id AS subscription_id, f.latest_candidate_json, f.tracking_state
            FROM subscriptions s JOIN flights f ON f.id=s.flight_id
            WHERE {" AND ".join(clauses)}
            ORDER BY s.id
            """,
            tuple(params),
        )

    async def find_fresh_candidates(
        self,
        *,
        flight_iata: str,
        flight_date: str | None,
        departure_iata: str | None,
        fresh_after_epoch: int,
    ) -> list[FlightCandidate]:
        clauses = ["requested_flight_iata=?", "last_success_at_epoch>=?"]
        params: list[Any] = [flight_iata, fresh_after_epoch]
        if flight_date:
            clauses.append("flight_date=?")
            params.append(flight_date)
        if departure_iata:
            clauses.append("departure_iata=?")
            params.append(departure_iata)
        rows = await self.fetchall(
            f"SELECT latest_candidate_json FROM flights WHERE {' AND '.join(clauses)} ORDER BY id",
            tuple(params),
        )
        return [FlightCandidate.from_dict(json.loads(row["latest_candidate_json"])) for row in rows]

    @staticmethod
    def instance_key(candidate: FlightCandidate) -> str:
        scheduled = candidate.scheduled_departure.local_iso if candidate.scheduled_departure else ""
        canonical = "|".join(
            (
                candidate.requested_flight_iata,
                candidate.flight_date,
                candidate.departure_iata,
                candidate.arrival_iata,
                scheduled,
                candidate.departure_timezone or "",
            )
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def create_or_get_flight(self, candidate: FlightCandidate) -> tuple[int, bool]:
        now = _now()
        state = candidate_to_state(candidate, fetched_at_epoch=now)
        key = self.instance_key(candidate)
        initial_tracking_state = {
            "landed": "finished_landed",
            "cancelled": "finished_cancelled",
            "active": "active",
            "incident": "incident",
            "diverted": "diverted",
        }.get(
            candidate.api_status,
            "future_scheduled" if candidate.source_kind == "schedule" else "scheduled",
        )
        scheduled_local = (
            candidate.scheduled_departure.local_iso if candidate.scheduled_departure else None
        )
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            cursor = await self.conn.execute(
                "SELECT id, tracking_state FROM flights WHERE instance_key=?", (key,)
            )
            existing = await cursor.fetchone()
            if existing:
                flight_id = int(existing["id"])
                if existing["tracking_state"] == "paused_no_subscribers":
                    await self.conn.execute(
                        "UPDATE flights SET tracking_state=?, next_check_at_epoch=?, updated_at_epoch=? WHERE id=?",
                        (initial_tracking_state, now, now, flight_id),
                    )
                await self.conn.commit()
                return flight_id, False
            record = {
                "instance_key": key,
                "requested_flight_iata": candidate.requested_flight_iata,
                "provider_flight_iata": candidate.provider_flight_iata,
                "flight_date": candidate.flight_date,
                "identity_departure_iata": candidate.departure_iata,
                "identity_arrival_iata": candidate.arrival_iata,
                "identity_scheduled_departure_local": scheduled_local,
                "identity_departure_timezone": candidate.departure_timezone,
                "airline_name": candidate.airline_name,
                "departure_airport": candidate.departure_airport,
                "departure_iata": candidate.departure_iata,
                "departure_icao": candidate.departure_icao,
                "departure_timezone": candidate.departure_timezone,
                "arrival_airport": candidate.arrival_airport,
                "arrival_iata": candidate.arrival_iata,
                "arrival_icao": candidate.arrival_icao,
                "arrival_timezone": candidate.arrival_timezone,
                "scheduled_departure_local": scheduled_local,
                "scheduled_departure_utc_epoch": _time_field(
                    candidate, "scheduled_departure", "utc_epoch"
                ),
                "estimated_departure_local": _time_field(
                    candidate, "estimated_departure", "local_iso"
                ),
                "estimated_departure_utc_epoch": _time_field(
                    candidate, "estimated_departure", "utc_epoch"
                ),
                "actual_departure_local": _time_field(candidate, "actual_departure", "local_iso"),
                "actual_departure_utc_epoch": _time_field(
                    candidate, "actual_departure", "utc_epoch"
                ),
                "scheduled_arrival_local": _time_field(candidate, "scheduled_arrival", "local_iso"),
                "scheduled_arrival_utc_epoch": _time_field(
                    candidate, "scheduled_arrival", "utc_epoch"
                ),
                "estimated_arrival_local": _time_field(candidate, "estimated_arrival", "local_iso"),
                "estimated_arrival_utc_epoch": _time_field(
                    candidate, "estimated_arrival", "utc_epoch"
                ),
                "actual_arrival_local": _time_field(candidate, "actual_arrival", "local_iso"),
                "actual_arrival_utc_epoch": _time_field(candidate, "actual_arrival", "utc_epoch"),
                "departure_delay_minutes": candidate.departure_delay,
                "arrival_delay_minutes": candidate.arrival_delay,
                "departure_terminal": candidate.departure_terminal,
                "departure_gate": candidate.departure_gate,
                "arrival_terminal": candidate.arrival_terminal,
                "arrival_gate": candidate.arrival_gate,
                "arrival_baggage": candidate.arrival_baggage,
                "api_status": candidate.api_status,
                "aircraft_registration": candidate.aircraft_registration,
                "codeshare_json": _json(candidate.codeshare),
                "normalized_state_json": _json(state),
                "latest_candidate_json": _json(candidate.to_dict()),
                "last_raw_flight_json": _json(candidate.raw),
                "last_checked_at_epoch": now,
                "last_success_at_epoch": now,
                "next_check_at_epoch": now,
                "tracking_state": initial_tracking_state,
                "finished_reason": (
                    "landed"
                    if candidate.api_status == "landed"
                    else ("cancelled" if candidate.api_status == "cancelled" else None)
                ),
                "finished_at_epoch": (
                    now if candidate.api_status in {"landed", "cancelled"} else None
                ),
                "created_at_epoch": now,
                "updated_at_epoch": now,
            }
            columns = ", ".join(record)
            placeholders = ", ".join("?" for _ in record)
            cursor = await self.conn.execute(
                f"INSERT INTO flights({columns}) VALUES({placeholders})",
                tuple(record.values()),
            )
            await self.conn.commit()
            return int(cursor.lastrowid), True

    async def set_flight_schedule(self, flight_id: int, next_epoch: int, priority: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE flights SET next_check_at_epoch=?, polling_priority=?, updated_at_epoch=? WHERE id=?",
                (next_epoch, priority, _now(), flight_id),
            )
            await self.conn.commit()

    async def add_subscription(
        self, flight_id: int, user_id: int, chat_id: int
    ) -> tuple[int, bool]:
        now = _now()
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            cursor = await self.conn.execute(
                "SELECT id, active FROM subscriptions WHERE flight_id=? AND telegram_user_id=? AND telegram_chat_id=?",
                (flight_id, user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row:
                subscription_id = int(row["id"])
                created = not bool(row["active"])
                await self.conn.execute(
                    "UPDATE subscriptions SET active=1, stopped_at_epoch=NULL, stop_reason=NULL WHERE id=?",
                    (subscription_id,),
                )
            else:
                cursor = await self.conn.execute(
                    """
                    INSERT INTO subscriptions(flight_id, telegram_user_id, telegram_chat_id, active, created_at_epoch)
                    VALUES(?, ?, ?, 1, ?)
                    """,
                    (flight_id, user_id, chat_id, now),
                )
                subscription_id = int(cursor.lastrowid)
                created = True
            await self.conn.commit()
        return subscription_id, created

    async def get_flight(self, flight_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM flights WHERE id=?", (flight_id,))

    async def list_user_subscriptions(
        self, user_id: int, chat_id: int, *, limit: int = 20, offset: int = 0
    ) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT s.id AS subscription_id, s.active, f.*
            FROM subscriptions s JOIN flights f ON f.id=s.flight_id
            WHERE s.telegram_user_id=? AND s.telegram_chat_id=? AND s.active=1
            ORDER BY f.flight_date, f.scheduled_departure_utc_epoch, s.id
            LIMIT ? OFFSET ?
            """,
            (user_id, chat_id, limit, offset),
        )

    async def stop_subscription(
        self,
        subscription_id: int,
        user_id: int,
        *,
        chat_id: int | None = None,
        reason: str = "user",
    ) -> bool:
        now = _now()
        chat_clause = " AND telegram_chat_id=?" if chat_id is not None else ""
        params = (
            (subscription_id, user_id, chat_id)
            if chat_id is not None
            else (subscription_id, user_id)
        )
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            cursor = await self.conn.execute(
                f"SELECT flight_id FROM subscriptions "
                f"WHERE id=? AND telegram_user_id=?{chat_clause} AND active=1",
                params,
            )
            row = await cursor.fetchone()
            if not row:
                await self.conn.rollback()
                return False
            flight_id = int(row["flight_id"])
            await self.conn.execute(
                "UPDATE subscriptions SET active=0, stopped_at_epoch=?, stop_reason=? WHERE id=?",
                (now, reason, subscription_id),
            )
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS count FROM subscriptions WHERE flight_id=? AND active=1",
                (flight_id,),
            )
            count_row = await cursor.fetchone()
            if count_row and int(count_row["count"]) == 0:
                await self.conn.execute(
                    """
                    UPDATE flights
                    SET tracking_state='paused_no_subscribers', lease_owner=NULL,
                        lease_until_epoch=NULL, updated_at_epoch=?
                    WHERE id=? AND tracking_state IN ('scheduled','active','incident','diverted','post_landing','future_scheduled')
                    """,
                    (now, flight_id),
                )
            await self.conn.commit()
            return True

    async def deactivate_chat(self, chat_id: int, *, reason: str) -> None:
        rows = await self.fetchall(
            "SELECT id, telegram_user_id FROM subscriptions WHERE telegram_chat_id=? AND active=1",
            (chat_id,),
        )
        for row in rows:
            await self.stop_subscription(
                int(row["id"]), int(row["telegram_user_id"]), reason=reason
            )

    async def delete_user(self, user_id: int) -> None:
        rows = await self.fetchall(
            "SELECT id FROM subscriptions WHERE telegram_user_id=? AND active=1", (user_id,)
        )
        for row in rows:
            await self.stop_subscription(int(row["id"]), user_id, reason="delete_me")
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM date_selection_sessions WHERE telegram_user_id=?", (user_id,)
            )
            await self.conn.execute(
                "DELETE FROM pending_selections WHERE telegram_user_id=?", (user_id,)
            )
            await self.conn.execute(
                "DELETE FROM subscriptions WHERE telegram_user_id=?", (user_id,)
            )
            await self.conn.commit()

    async def claim_due_flights(
        self, *, owner: str, now_epoch: int, lease_seconds: int, limit: int
    ) -> list[aiosqlite.Row]:
        placeholders = ",".join("?" for _ in POLLABLE_STATES)
        params: tuple[Any, ...] = (*POLLABLE_STATES, now_epoch, now_epoch, limit)
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            cursor = await self.conn.execute(
                f"""
                SELECT f.* FROM flights f
                WHERE f.tracking_state IN ({placeholders})
                  AND f.next_check_at_epoch<=?
                  AND (f.lease_until_epoch IS NULL OR f.lease_until_epoch<?)
                  AND EXISTS(SELECT 1 FROM subscriptions s WHERE s.flight_id=f.id AND s.active=1)
                ORDER BY f.polling_priority DESC, f.next_check_at_epoch ASC
                LIMIT ?
                """,
                params,
            )
            rows = list(await cursor.fetchall())
            for row in rows:
                await self.conn.execute(
                    "UPDATE flights SET lease_owner=?, lease_until_epoch=? WHERE id=?",
                    (owner, now_epoch + lease_seconds, int(row["id"])),
                )
            await self.conn.commit()
            return rows

    async def mark_poll_failure(
        self,
        flight_id: int,
        *,
        owner: str,
        next_check_at_epoch: int,
        not_found: bool,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE flights
                SET consecutive_failures=consecutive_failures+?,
                    consecutive_not_found=consecutive_not_found+?,
                    last_checked_at_epoch=?, next_check_at_epoch=?,
                    lease_owner=NULL, lease_until_epoch=NULL, updated_at_epoch=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    0 if not_found else 1,
                    1 if not_found else 0,
                    _now(),
                    next_check_at_epoch,
                    _now(),
                    flight_id,
                    owner,
                ),
            )
            await self.conn.commit()

    async def suspend_flight(self, flight_id: int, *, owner: str, state: str) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE flights SET tracking_state=?, lease_owner=NULL, lease_until_epoch=NULL,
                    suspended_previous_state=tracking_state,
                    last_checked_at_epoch=?, updated_at_epoch=?
                WHERE id=? AND lease_owner=?
                """,
                (state, _now(), _now(), flight_id, owner),
            )
            await self.conn.commit()

    async def resume_suspended_quota(self, *, next_check_at_epoch: int) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                UPDATE flights
                SET tracking_state=COALESCE(
                        suspended_previous_state,
                        CASE
                            WHEN api_status='active' THEN 'active'
                            WHEN api_status='incident' THEN 'incident'
                            WHEN api_status='diverted' THEN 'diverted'
                            ELSE 'scheduled'
                        END
                    ),
                    suspended_previous_state=NULL,
                    next_check_at_epoch=?, updated_at_epoch=?
                WHERE tracking_state='suspended_quota'
                  AND EXISTS(SELECT 1 FROM subscriptions s WHERE s.flight_id=flights.id AND s.active=1)
                """,
                (next_check_at_epoch, _now()),
            )
            await self.conn.commit()
            return cursor.rowcount

    async def release_lease(self, flight_id: int, *, owner: str, next_check_at_epoch: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE flights SET lease_owner=NULL, lease_until_epoch=NULL,
                    next_check_at_epoch=?, updated_at_epoch=?
                WHERE id=? AND lease_owner=?
                """,
                (next_check_at_epoch, _now(), flight_id, owner),
            )
            await self.conn.commit()

    async def apply_poll_success(
        self,
        *,
        flight_id: int,
        owner: str,
        candidate: FlightCandidate,
        normalized_state: dict[str, Any],
        tracking_state: str,
        next_check_at_epoch: int,
        polling_priority: int,
        event_kind: str | None,
        event_text: str | None,
        finished_reason: str | None,
        landed_seen_at_epoch: int | None,
    ) -> None:
        now = _now()
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            cursor = await self.conn.execute(
                "SELECT state_version FROM flights WHERE id=? AND lease_owner=?", (flight_id, owner)
            )
            row = await cursor.fetchone()
            if not row:
                await self.conn.rollback()
                return
            state_version = int(row["state_version"]) + 1
            finished_at = now if tracking_state.startswith("finished_") else None
            await self.conn.execute(
                """
                UPDATE flights SET
                    provider_flight_iata=?, airline_name=?, departure_airport=?, departure_iata=?,
                    departure_icao=?, departure_timezone=?, arrival_airport=?, arrival_iata=?,
                    arrival_icao=?, arrival_timezone=?, scheduled_departure_local=?,
                    scheduled_departure_utc_epoch=?, estimated_departure_local=?,
                    estimated_departure_utc_epoch=?, actual_departure_local=?, actual_departure_utc_epoch=?,
                    scheduled_arrival_local=?, scheduled_arrival_utc_epoch=?, estimated_arrival_local=?,
                    estimated_arrival_utc_epoch=?, actual_arrival_local=?, actual_arrival_utc_epoch=?,
                    departure_delay_minutes=?, arrival_delay_minutes=?, departure_terminal=?, departure_gate=?,
                    arrival_terminal=?, arrival_gate=?, arrival_baggage=?, api_status=?,
                    aircraft_registration=?, codeshare_json=?, normalized_state_json=?,
                    latest_candidate_json=?, last_raw_flight_json=?, state_version=?,
                    last_checked_at_epoch=?, last_success_at_epoch=?, next_check_at_epoch=?,
                    polling_priority=?, consecutive_failures=0, consecutive_not_found=0,
                    lease_owner=NULL, lease_until_epoch=NULL, tracking_state=?, finished_reason=?,
                    finished_at_epoch=?, landed_seen_at_epoch=COALESCE(landed_seen_at_epoch, ?),
                    updated_at_epoch=?
                WHERE id=? AND lease_owner=?
                """,
                (
                    candidate.provider_flight_iata,
                    candidate.airline_name,
                    candidate.departure_airport,
                    candidate.departure_iata,
                    candidate.departure_icao,
                    candidate.departure_timezone,
                    candidate.arrival_airport,
                    candidate.arrival_iata,
                    candidate.arrival_icao,
                    candidate.arrival_timezone,
                    _time_field(candidate, "scheduled_departure", "local_iso"),
                    _time_field(candidate, "scheduled_departure", "utc_epoch"),
                    _time_field(candidate, "estimated_departure", "local_iso"),
                    _time_field(candidate, "estimated_departure", "utc_epoch"),
                    _time_field(candidate, "actual_departure", "local_iso"),
                    _time_field(candidate, "actual_departure", "utc_epoch"),
                    _time_field(candidate, "scheduled_arrival", "local_iso"),
                    _time_field(candidate, "scheduled_arrival", "utc_epoch"),
                    _time_field(candidate, "estimated_arrival", "local_iso"),
                    _time_field(candidate, "estimated_arrival", "utc_epoch"),
                    _time_field(candidate, "actual_arrival", "local_iso"),
                    _time_field(candidate, "actual_arrival", "utc_epoch"),
                    candidate.departure_delay,
                    candidate.arrival_delay,
                    candidate.departure_terminal,
                    candidate.departure_gate,
                    candidate.arrival_terminal,
                    candidate.arrival_gate,
                    candidate.arrival_baggage,
                    candidate.api_status,
                    candidate.aircraft_registration,
                    _json(candidate.codeshare),
                    _json(normalized_state),
                    _json(candidate.to_dict()),
                    _json(candidate.raw),
                    state_version,
                    now,
                    now,
                    next_check_at_epoch,
                    polling_priority,
                    tracking_state,
                    finished_reason,
                    finished_at,
                    landed_seen_at_epoch,
                    now,
                    flight_id,
                    owner,
                ),
            )
            if event_kind and event_text:
                cursor = await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_events(
                        flight_id, state_version, event_kind, payload_json, created_at_epoch
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (flight_id, state_version, event_kind, _json({"text": event_text}), now),
                )
                if cursor.rowcount:
                    event_id = int(cursor.lastrowid)
                    await self.conn.execute(
                        """
                        INSERT OR IGNORE INTO notification_deliveries(
                            event_id, subscription_id, telegram_chat_id, status,
                            next_attempt_at_epoch, created_at_epoch, updated_at_epoch
                        )
                        SELECT ?, MIN(id), telegram_chat_id, 'pending', ?, ?, ?
                        FROM subscriptions
                        WHERE flight_id=? AND active=1
                        GROUP BY telegram_chat_id
                        """,
                        (event_id, now, now, now, flight_id),
                    )
            await self.conn.commit()

    async def get_due_deliveries(self, *, limit: int = 50) -> list[aiosqlite.Row]:
        return await self.fetchall(
            """
            SELECT d.*, e.payload_json, e.event_kind
            FROM notification_deliveries d
            JOIN notification_events e ON e.id=d.event_id
            WHERE d.status IN ('pending','retry') AND d.next_attempt_at_epoch<=?
              AND EXISTS(
                  SELECT 1 FROM subscriptions s
                  WHERE s.flight_id=e.flight_id
                    AND s.telegram_chat_id=d.telegram_chat_id
                    AND s.active=1
              )
            ORDER BY d.next_attempt_at_epoch, d.id
            LIMIT ?
            """,
            (_now(), limit),
        )

    async def mark_delivery_sent(self, delivery_id: int, message_id: int) -> None:
        now = _now()
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE notification_deliveries
                SET status='sent', attempt_count=attempt_count+1, telegram_message_id=?,
                    sent_at_epoch=?, updated_at_epoch=? WHERE id=?
                """,
                (message_id, now, now, delivery_id),
            )
            await self.conn.commit()

    async def mark_delivery_retry(
        self, delivery_id: int, *, next_attempt_at_epoch: int, error_code: str
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE notification_deliveries
                SET status='retry', attempt_count=attempt_count+1, next_attempt_at_epoch=?,
                    last_error_code=?, updated_at_epoch=? WHERE id=?
                """,
                (next_attempt_at_epoch, error_code, _now(), delivery_id),
            )
            await self.conn.commit()

    async def mark_delivery_failed(self, delivery_id: int, *, error_code: str) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE notification_deliveries
                SET status='failed', attempt_count=attempt_count+1,
                    last_error_code=?, updated_at_epoch=? WHERE id=?
                """,
                (error_code, _now(), delivery_id),
            )
            await self.conn.commit()

    async def cleanup(
        self,
        *,
        pending_before: int,
        raw_before: int,
        api_before: int,
        deliveries_before: int,
        finished_before: int,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM date_selection_sessions WHERE expires_at_epoch<?", (pending_before,)
            )
            await self.conn.execute(
                "DELETE FROM pending_selections WHERE expires_at_epoch<?", (pending_before,)
            )
            await self.conn.execute(
                "UPDATE flights SET last_raw_flight_json='{}' WHERE updated_at_epoch<?",
                (raw_before,),
            )
            await self.conn.execute(
                "DELETE FROM api_requests WHERE created_at_epoch<?", (api_before,)
            )
            await self.conn.execute(
                "DELETE FROM notification_events WHERE created_at_epoch<?", (deliveries_before,)
            )
            await self.conn.execute(
                """
                DELETE FROM flights
                WHERE finished_at_epoch<?
                  AND NOT EXISTS(SELECT 1 FROM subscriptions s WHERE s.flight_id=flights.id AND s.active=1)
                """,
                (finished_before,),
            )
            await self.conn.commit()
