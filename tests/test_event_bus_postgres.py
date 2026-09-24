"""Real PostgreSQL concurrency and atomicity checks for the event bus."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from wallapop_tracker.services.event_bus import EventBus
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    DeadLetterRecord,
    DomainEventRecord,
    EventConsumptionRecord,
    EventConsumptionStatus,
    ListingRecord,
    NotificationDeliveryRecord,
    ProfileRecord,
    TrackingEventRecord,
    TrackingRunRecord,
)
from wallapop_tracker.storage.repositories import NotificationDeliveryRepository

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def postgres_database():
    url = os.getenv("WALLAPOP_TRACKER_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("WALLAPOP_TRACKER_TEST_POSTGRES_URL is not configured")
    database = Database(url)
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        database.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def clean_event_bus_rows(postgres_database):
    with postgres_database.engine.begin() as connection:
        connection.execute(text("DELETE FROM dead_letters"))
        connection.execute(text("DELETE FROM event_consumptions"))
        connection.execute(text("DELETE FROM domain_events"))


def publish(database: Database) -> int:
    with database.transaction() as session:
        event, _ = EventBus.publish(
            session,
            event_type="ListingObserved",
            aggregate_type="listing",
            aggregate_id=uuid4().hex,
            payload={"fixture": True},
            idempotency_key=uuid4().hex,
        )
        return event.id


def claim(url: str, consumer: str, barrier: Barrier) -> tuple[int, str] | None:
    database = Database(url)
    try:
        with database.transaction() as session:
            barrier.wait()
            result = EventBus(database).claim_next(session, consumer=consumer, lease_seconds=60)
            return (result[0].id, result[1].claimed_by or "") if result else None
    finally:
        database.close()


def test_postgres_skip_locked_same_consumer_claims_once(postgres_database):
    url = postgres_database.engine.url.render_as_string(hide_password=False)
    event_ids = {publish(postgres_database) for _ in range(8)}
    consumer = "analytics-" + uuid4().hex[:12]
    with ThreadPoolExecutor(max_workers=2) as executor:
        barrier = Barrier(2)
        results = list(executor.map(lambda _: claim(url, consumer, barrier), range(2)))
    claimed_ids = {result[0] for result in results if result is not None}
    assert len(claimed_ids) == 2
    with postgres_database.session() as session:
        assert (
            session.scalar(
                select(text("count(*)"))
                .select_from(EventConsumptionRecord.__table__)
                .where(
                    EventConsumptionRecord.event_id.in_(event_ids),
                    EventConsumptionRecord.consumer_name == consumer,
                )
            )
            == 2
        )


def test_postgres_consumers_are_independent(postgres_database):
    event_id = publish(postgres_database)
    with postgres_database.transaction() as session:
        analytics = EventBus(postgres_database).claim_next(session, consumer="analytics")
    with postgres_database.transaction() as session:
        notifications = EventBus(postgres_database).claim_next(session, consumer="notifications")
    assert analytics is not None and analytics[0].id == event_id
    assert notifications is not None and notifications[0].id == event_id


def test_postgres_lease_expiry_reclaim_and_dlq_requeue(postgres_database):
    event_id = publish(postgres_database)
    consumer = "recovery-" + uuid4().hex[:12]
    with postgres_database.transaction() as session:
        first = EventBus(postgres_database, worker_id="dead").claim_next(
            session, consumer=consumer, lease_seconds=1
        )
        assert first is not None and first[0].id == event_id
        first[1].lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with postgres_database.transaction() as session:
        recovered = EventBus(postgres_database, worker_id="recovered").claim_next(
            session, consumer=consumer
        )
        assert recovered is not None and recovered[1].attempts == 2
    failing = EventBus(postgres_database)
    assert failing.consume_once(
        "dlq-" + consumer, lambda _: (_ for _ in ()).throw(RuntimeError("boom")), max_attempts=1
    )
    with postgres_database.session() as session:
        dead = session.scalar(select(DeadLetterRecord))
        assert dead is not None
    with postgres_database.transaction() as session:
        failing.requeue(session, dead.id)
    with postgres_database.session() as session:
        state = session.scalar(
            select(EventConsumptionRecord).where(
                EventConsumptionRecord.event_id == dead.event_id,
                EventConsumptionRecord.consumer_name == dead.consumer_name,
            )
        )
        assert session.get(DeadLetterRecord, dead.id).requeue_count == 1
        assert state is not None and state.status == EventConsumptionStatus.PENDING


def test_postgres_outbox_rollback_is_atomic(postgres_database):
    alias = "atomic-" + uuid4().hex[:12]
    with pytest.raises(RuntimeError):
        with postgres_database.transaction() as session:
            now = datetime.now(UTC)
            session.add(
                ProfileRecord(
                    wallapop_user_id=alias,
                    first_seen_at=now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            EventBus.publish(
                session,
                event_type="ListingCreated",
                aggregate_type="profile",
                aggregate_id=alias,
                idempotency_key=alias,
            )
            raise RuntimeError("rollback")
    with postgres_database.session() as session:
        assert (
            session.scalar(select(ProfileRecord).where(ProfileRecord.wallapop_user_id == alias))
            is None
        )
        assert (
            session.scalar(
                select(DomainEventRecord).where(DomainEventRecord.idempotency_key == alias)
            )
            is None
        )


def test_postgres_notification_delivery_idempotency(postgres_database):
    now = datetime.now(UTC)
    suffix = uuid4().hex[:12]
    with postgres_database.transaction() as session:
        profile = ProfileRecord(
            wallapop_user_id="notify-profile-" + suffix,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
        run = TrackingRunRecord(profile_id=profile.id, started_at=now)
        session.add(run)
        session.flush()
        listing = ListingRecord(
            external_id="notify-listing-" + suffix,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(listing)
        session.flush()
        event = TrackingEventRecord(
            event_type="NEW_LISTING",
            idempotency_key="notify-" + suffix,
            listing_id=listing.id,
            tracking_run_id=run.id,
            created_at=now,
        )
        session.add(event)
        session.flush()
        repository = NotificationDeliveryRepository(session)
        first, created = repository.create_once(
            event_id=event.id, channel="webhook", destination="test", created_at=now
        )
        second, duplicate = repository.create_once(
            event_id=event.id, channel="webhook", destination="test", created_at=now
        )
        assert created and not duplicate and first.id == second.id
        assert (
            session.scalar(
                select(NotificationDeliveryRecord).where(
                    NotificationDeliveryRecord.event_id == event.id
                )
            )
            is not None
        )
