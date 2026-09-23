"""Real PostgreSQL checks; use a disposable database URL in CI/local runs."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from alembic import command
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    ProfileRecord,
    TrackedProfileRecord,
    TrackingEventRecord,
    TrackingRunRecord,
)
from wallapop_tracker.storage.repositories import (
    NotificationDeliveryRepository,
    TrackingEventRepository,
    claim_tracking_jobs,
    release_tracking_claim,
)

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def postgres_url():
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
    database.close()
    return url


def test_postgres_alembic_upgrade_and_tracking_claim(postgres_url):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    assert config.get_main_option("sqlalchemy.url") == postgres_url
    bootstrap_engine = Database(postgres_url).engine
    try:
        with bootstrap_engine.connect() as connection:
            before = connection.execute(
                text("SELECT current_database(), current_schema(), current_setting('search_path')")
            ).one()
            assert before[0] == connection.engine.url.database
            assert before[1] == "public"
        assert bootstrap_engine.url.render_as_string(hide_password=False) == postgres_url
    finally:
        bootstrap_engine.dispose()
    command.upgrade(config, "head")
    database = Database(postgres_url)
    now = datetime.now(UTC)
    try:
        inspector = inspect(database.engine)
        tables = set(inspector.get_table_names(schema="public"))
        assert {
            "alembic_version",
            "tracked_profiles",
            "tracked_listings",
            "tracking_runs",
            "notification_deliveries",
        } <= tables
        with database.engine.connect() as connection:
            after = connection.execute(
                text("SELECT current_database(), current_schema(), current_setting('search_path')")
            ).one()
        assert after[0] == database.engine.url.database
        assert after[1] == "public"
        with database.transaction() as session:
            session.add(
                TrackedProfileRecord(
                    profile_url="https://es.wallapop.com/user/postgres-test",
                    wallapop_user_id="postgres-test",
                    alias="postgres-test",
                    added_at=now,
                )
            )
        with database.transaction() as session:
            claimed, _, _ = claim_tracking_jobs(
                session,
                now=now,
                worker_id="postgres-worker-a",
                lease_seconds=60,
                interval=timedelta(hours=1),
            )
            assert len(claimed) == 1
        with database.transaction() as session:
            claimed, _, _ = claim_tracking_jobs(
                session,
                now=now,
                worker_id="postgres-worker-b",
                lease_seconds=60,
                interval=timedelta(hours=1),
            )
            assert claimed == []
    finally:
        with database.transaction() as session:
            session.query(TrackedProfileRecord).filter_by(alias="postgres-test").delete()
        database.close()


def _seed_tracking_profile(database: Database, suffix: str) -> str:
    alias = f"postgres-{suffix}"
    with database.transaction() as session:
        session.add(
            TrackedProfileRecord(
                profile_url=f"https://es.wallapop.com/user/{alias}",
                wallapop_user_id=alias,
                alias=alias,
                added_at=datetime.now(UTC),
            )
        )
    return alias


def _concurrent_claim(url: str, alias: str, barrier: object) -> str | None:
    database = Database(url)
    try:
        with database.session() as session:
            session.execute(
                select(TrackedProfileRecord.id).where(TrackedProfileRecord.alias == alias)
            ).all()
            barrier.wait()
            claimed, _, _ = claim_tracking_jobs(
                session,
                now=datetime.now(UTC),
                worker_id=f"worker-{uuid4().hex}",
                lease_seconds=60,
                interval=timedelta(hours=1),
            )
            worker = claimed[0].claimed_by if claimed else None
            session.commit()
            return worker
    finally:
        database.close()


def test_postgres_tracking_claim_skip_locked_expiry_and_ownership(postgres_url):
    database = Database(postgres_url)
    alias = _seed_tracking_profile(database, uuid4().hex[:12])
    try:
        from threading import Barrier

        with ThreadPoolExecutor(max_workers=2) as executor:
            barrier = Barrier(2)
            results = list(
                executor.map(lambda _: _concurrent_claim(postgres_url, alias, barrier), range(2))
            )
        assert sum(result is not None for result in results) == 1
        with database.transaction() as session:
            record = session.scalar(
                select(TrackedProfileRecord).where(TrackedProfileRecord.alias == alias)
            )
            assert record is not None
            owner = record.claimed_by
            assert owner is not None
            release_tracking_claim(session, TrackedProfileRecord, record.id, "wrong-worker")
            assert record.claimed_by == owner
            record.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with database.transaction() as session:
            claimed, _, _ = claim_tracking_jobs(
                session,
                now=datetime.now(UTC),
                worker_id="recovery-worker",
                lease_seconds=60,
                interval=timedelta(hours=1),
            )
            assert [record.claimed_by for record in claimed] == ["recovery-worker"]
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM tracked_profiles WHERE alias = :alias"), {"alias": alias}
            )
        database.close()


def _seed_delivery(database: Database, suffix: str) -> tuple[int, int]:
    now = datetime.now(UTC)
    with database.transaction() as session:
        profile = ProfileRecord(
            wallapop_user_id=f"delivery-profile-{suffix}",
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
            external_id=f"delivery-listing-{suffix}",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(listing)
        session.flush()
        event = TrackingEventRecord(
            event_type="NEW_LISTING",
            idempotency_key=f"delivery-event-{suffix}",
            listing_id=listing.id,
            tracking_run_id=run.id,
            created_at=now,
        )
        session.add(event)
        session.flush()
        delivery = NotificationDeliveryRecord(
            event_id=event.id,
            channel="webhook",
            destination=f"destination-{suffix}",
            status=NotificationDeliveryStatus.PENDING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(delivery)
        session.flush()
        return event.id, delivery.id


def _concurrent_delivery_claim(url: str, delivery_id: int, barrier: object) -> str | None:
    database = Database(url)
    try:
        with database.session() as session:
            session.execute(
                select(NotificationDeliveryRecord.id).where(
                    NotificationDeliveryRecord.id == delivery_id
                )
            ).all()
            barrier.wait()
            delivery = NotificationDeliveryRepository(session).claim_next(
                now=datetime.now(UTC),
                worker_id=f"notification-{uuid4().hex}",
                lease_seconds=60,
                max_attempts=3,
            )
            owner = delivery.claimed_by if delivery else None
            session.commit()
            return owner
    finally:
        database.close()


def test_postgres_notification_claim_retry_and_completed_delivery(postgres_url):
    database = Database(postgres_url)
    event_id, delivery_id = _seed_delivery(database, uuid4().hex[:12])
    try:
        from threading import Barrier

        with ThreadPoolExecutor(max_workers=2) as executor:
            barrier = Barrier(2)
            results = list(
                executor.map(
                    lambda _: _concurrent_delivery_claim(postgres_url, delivery_id, barrier),
                    range(2),
                )
            )
        assert sum(result is not None for result in results) == 1
        with database.transaction() as session:
            delivery = session.get(NotificationDeliveryRecord, delivery_id)
            assert delivery is not None
            delivery.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with database.transaction() as session:
            delivery = NotificationDeliveryRepository(session).claim_next(
                now=datetime.now(UTC),
                worker_id="notification-recovery",
                lease_seconds=60,
                max_attempts=3,
            )
            assert delivery is not None
            delivery.status = NotificationDeliveryStatus.FAILED
            delivery.claimed_by = None
            delivery.claim_expires_at = None
            delivery.next_attempt_at = datetime.now(UTC) + timedelta(minutes=5)
        with database.transaction() as session:
            assert (
                NotificationDeliveryRepository(session).claim_next(
                    now=datetime.now(UTC),
                    worker_id="too-early",
                    lease_seconds=60,
                    max_attempts=3,
                )
                is None
            )
            delivery = session.get(NotificationDeliveryRecord, delivery_id)
            assert delivery is not None
            delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            delivery.status = NotificationDeliveryStatus.DELIVERED
            delivery.claimed_by = None
            delivery.claim_expires_at = None
        with database.transaction() as session:
            assert (
                NotificationDeliveryRepository(session).claim_next(
                    now=datetime.now(UTC),
                    worker_id="completed-reclaimer",
                    lease_seconds=60,
                    max_attempts=3,
                    include_failed=True,
                )
                is None
            )
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM notification_deliveries WHERE event_id = :event_id"),
                {"event_id": event_id},
            )
            connection.execute(
                text("DELETE FROM tracking_events WHERE id = :event_id"), {"event_id": event_id}
            )
            connection.execute(
                text(
                    "DELETE FROM tracking_runs WHERE id NOT IN "
                    "(SELECT tracking_run_id FROM tracking_events) AND profile_id IN "
                    "(SELECT id FROM profiles WHERE wallapop_user_id LIKE 'delivery-profile-%')"
                )
            )
            connection.execute(
                text("DELETE FROM listings WHERE external_id LIKE 'delivery-listing-%'")
            )
            connection.execute(
                text("DELETE FROM profiles WHERE wallapop_user_id LIKE 'delivery-profile-%'")
            )
        database.close()


def _concurrent_event(url: str, listing_id: int, run_id: int, key: str, barrier: object) -> bool:
    database = Database(url)
    try:
        with database.session() as session:
            barrier.wait()
            _, created = TrackingEventRepository(session).create_once(
                event_type="NEW_LISTING",
                idempotency_key=key,
                listing_id=listing_id,
                tracking_run_id=run_id,
                tracked_search_id=None,
                old_price=None,
                new_price=None,
                created_at=datetime.now(UTC),
            )
            session.commit()
            return created
    finally:
        database.close()


def test_postgres_tracking_and_delivery_idempotency_concurrency(postgres_url):
    database = Database(postgres_url)
    event_id, _ = _seed_delivery(database, uuid4().hex[:12])
    suffix = uuid4().hex[:12]
    try:
        with database.session() as session:
            event = session.get(TrackingEventRecord, event_id)
            assert event is not None
            listing_id, run_id = event.listing_id, event.tracking_run_id
        from threading import Barrier

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            created = list(
                executor.map(
                    lambda _: _concurrent_event(
                        postgres_url, listing_id, run_id, f"concurrent-{suffix}", barrier
                    ),
                    range(2),
                )
            )
        assert sum(created) == 1
        with database.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM tracking_events WHERE idempotency_key = :key"),
                    {"key": f"concurrent-{suffix}"},
                ).scalar_one()
                == 1
            )
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM notification_deliveries WHERE event_id IN "
                    "(SELECT id FROM tracking_events WHERE tracking_run_id IN "
                    "(SELECT id FROM tracking_runs WHERE profile_id IN "
                    "(SELECT id FROM profiles WHERE wallapop_user_id LIKE 'delivery-profile-%')))"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM tracking_events WHERE tracking_run_id IN "
                    "(SELECT id FROM tracking_runs WHERE profile_id IN "
                    "(SELECT id FROM profiles WHERE wallapop_user_id LIKE 'delivery-profile-%'))"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM tracking_runs WHERE profile_id IN "
                    "(SELECT id FROM profiles WHERE wallapop_user_id LIKE 'delivery-profile-%')"
                )
            )
            connection.execute(
                text("DELETE FROM listings WHERE external_id LIKE 'delivery-listing-%'")
            )
            connection.execute(
                text("DELETE FROM profiles WHERE wallapop_user_id LIKE 'delivery-profile-%'")
            )
        database.close()


def test_postgres_historical_upgrade_to_head(postgres_url):
    source_url = make_url(postgres_url)
    database_name = f"wallapop_hist_{uuid4().hex[:12]}"
    admin_url = source_url.set(database="postgres")
    admin_engine = Database(admin_url.render_as_string(hide_password=False)).engine
    isolated_url = source_url.set(database=database_name).render_as_string(hide_password=False)
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", isolated_url)
        command.upgrade(config, "0008_separate_tracking_run_sources")
        command.upgrade(config, "head")
        engine = Database(isolated_url).engine
        try:
            tables = set(inspect(engine).get_table_names(schema="public"))
            assert {"tracked_profiles", "notification_deliveries", "tracking_runs"} <= tables
        finally:
            engine.dispose()
    finally:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'REVOKE CONNECT ON DATABASE "{database_name}" FROM public')
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin_engine.dispose()
