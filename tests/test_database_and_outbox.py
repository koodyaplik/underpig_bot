from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import make_candidate

from app.aviationstack.normalize import candidate_to_state
from app.storage.db import Database


@pytest.mark.asyncio
async def test_migration_subscription_and_outbox(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.migrate()
        candidate = make_candidate()
        flight_id, created = await db.create_or_get_flight(candidate)
        assert created
        subscription_id, subscription_created = await db.add_subscription(flight_id, 10, 10)
        assert subscription_created

        rows = await db.claim_due_flights(
            owner="test-owner",
            now_epoch=int(time.time()) + 1,
            lease_seconds=120,
            limit=10,
        )
        assert len(rows) == 1
        state = candidate_to_state(candidate, fetched_at_epoch=int(time.time()))
        await db.apply_poll_success(
            flight_id=flight_id,
            owner="test-owner",
            candidate=candidate,
            normalized_state=state,
            tracking_state="scheduled",
            next_check_at_epoch=int(time.time()) + 600,
            polling_priority=75,
            event_kind="flight_changed",
            event_text="test event",
            finished_reason=None,
            landed_seen_at_epoch=None,
        )
        deliveries = await db.get_due_deliveries()
        assert len(deliveries) == 1
        assert int(deliveries[0]["subscription_id"]) == subscription_id
        assert json.loads(deliveries[0]["payload_json"])["text"] == "test event"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_last_unsubscribe_pauses_polling(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.migrate()
        flight_id, _ = await db.create_or_get_flight(make_candidate())
        subscription_id, _ = await db.add_subscription(flight_id, 10, 10)
        assert await db.stop_subscription(subscription_id, 10)
        flight = await db.get_flight(flight_id)
        assert flight["tracking_state"] == "paused_no_subscribers"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_group_subscriptions_are_scoped_and_delivered_once_per_chat(
    tmp_path: Path,
) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.migrate()
        candidate = make_candidate()
        flight_id, _ = await db.create_or_get_flight(candidate)
        first_id, _ = await db.add_subscription(flight_id, 10, -1001)
        await db.add_subscription(flight_id, 11, -1001)
        other_chat_id, _ = await db.add_subscription(flight_id, 10, -1002)

        first_chat = await db.list_user_subscriptions(10, -1001)
        second_chat = await db.list_user_subscriptions(10, -1002)
        assert [int(row["subscription_id"]) for row in first_chat] == [first_id]
        assert [int(row["subscription_id"]) for row in second_chat] == [other_chat_id]
        assert not await db.stop_subscription(other_chat_id, 10, chat_id=-1001)

        rows = await db.claim_due_flights(
            owner="group-test",
            now_epoch=int(time.time()) + 1,
            lease_seconds=120,
            limit=10,
        )
        assert len(rows) == 1
        await db.apply_poll_success(
            flight_id=flight_id,
            owner="group-test",
            candidate=candidate,
            normalized_state=candidate_to_state(candidate, fetched_at_epoch=int(time.time())),
            tracking_state="scheduled",
            next_check_at_epoch=int(time.time()) + 600,
            polling_priority=75,
            event_kind="flight_changed",
            event_text="group event",
            finished_reason=None,
            landed_seen_at_epoch=None,
        )

        deliveries = await db.get_due_deliveries()
        assert len(deliveries) == 2
        assert {int(row["telegram_chat_id"]) for row in deliveries} == {-1001, -1002}

        assert await db.stop_subscription(first_id, 10, chat_id=-1001)
        assert len(await db.get_due_deliveries()) == 2
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_quota_resume_restores_future_state(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    try:
        await db.migrate()
        candidate = make_candidate(source_kind="future")
        flight_id, _ = await db.create_or_get_flight(candidate)
        await db.add_subscription(flight_id, 10, 10)
        rows = await db.claim_due_flights(
            owner="test-owner",
            now_epoch=int(time.time()) + 1,
            lease_seconds=120,
            limit=10,
        )
        assert rows
        await db.suspend_flight(flight_id, owner="test-owner", state="suspended_quota")
        suspended = await db.get_flight(flight_id)
        assert suspended["tracking_state"] == "suspended_quota"
        await db.resume_suspended_quota(next_check_at_epoch=int(time.time()) + 30)
        resumed = await db.get_flight(flight_id)
        assert resumed["tracking_state"] == "future_scheduled"
    finally:
        await db.close()
