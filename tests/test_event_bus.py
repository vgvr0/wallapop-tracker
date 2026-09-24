from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from wallapop_tracker.cli import app
from wallapop_tracker.services.event_bus import EventBus, deserialize_event
from wallapop_tracker.storage.models import (
    DeadLetterRecord,
    DomainEventRecord,
    EventConsumptionRecord,
    EventConsumptionStatus,
)


def publish(database, key="fact-1"):
    with database.transaction() as session:
        return EventBus.publish(
            session,
            event_type="PriceChanged",
            aggregate_type="listing",
            aggregate_id=42,
            payload={"old": "20", "new": "15"},
            metadata={"source": "test"},
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            correlation_id="corr-1",
            causation_id="parent-1",
            idempotency_key=key,
        )


def test_publish_is_serializable_ordered_and_idempotent(database):
    first, created = publish(database)
    second, duplicate = publish(database)
    assert created and not duplicate
    assert second.id == first.id
    with database.session() as session:
        row = session.get(DomainEventRecord, first.id)
        assert row is not None
        assert deserialize_event(row)["payload"] == {"new": "15", "old": "20"}
        assert deserialize_event(row)["correlation_id"] == "corr-1"


def test_consumer_claim_process_and_independent_state(database):
    event, _ = publish(database)
    bus = EventBus(database, worker_id="worker-a")
    seen: list[int] = []
    assert bus.consume_once("analytics", lambda item: seen.append(item.id))
    assert seen == [event.id]
    with database.session() as session:
        row = session.scalar(select(EventConsumptionRecord))
        assert row is not None and row.status == EventConsumptionStatus.PROCESSED
        assert bus.claim_next(session, consumer="notifications") is not None


def test_failure_backoff_dlq_and_auditable_requeue(database):
    event, _ = publish(database)
    bus = EventBus(database, worker_id="worker-a")
    assert bus.consume_once(
        "analytics", lambda _: (_ for _ in ()).throw(RuntimeError("boom")), max_attempts=1
    )
    with database.session() as session:
        dead = session.scalar(select(DeadLetterRecord))
        assert dead is not None and dead.event_id == event.id
        assert session.scalar(select(EventConsumptionRecord)).status == EventConsumptionStatus.DLQ
    with database.transaction() as session:
        bus.requeue(session, dead.id)
    with database.session() as session:
        dead = session.get(DeadLetterRecord, dead.id)
        assert dead is not None and dead.requeued_at is not None and dead.requeue_count == 1
        assert (
            session.scalar(select(EventConsumptionRecord)).status == EventConsumptionStatus.PENDING
        )


def test_expired_lease_is_reclaimed(database):
    publish(database)
    first = EventBus(database, worker_id="worker-a")
    second = EventBus(database, worker_id="worker-b")
    with database.transaction() as session:
        claimed = first.claim_next(session, consumer="analytics", lease_seconds=1)
        assert claimed is not None
        claimed[1].lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with database.transaction() as session:
        reclaimed = second.claim_next(session, consumer="analytics", lease_seconds=30)
        assert reclaimed is not None
        assert reclaimed[1].claimed_by == "worker-b"


def test_replay_rejects_side_effects_and_allows_analytics(database):
    publish(database)
    with database.transaction() as session:
        with pytest.raises(ValueError):
            EventBus(database).replay(session, consumer="notifications", from_id=1, to_id=1)
        assert EventBus(database).replay(session, consumer="analytics", from_id=1, to_id=1) == 1


def test_event_bus_cli_commands_are_exposed():
    runner = CliRunner()
    result = runner.invoke(app, ["events", "--help"])
    assert result.exit_code == 0
    assert "consume" in result.output
    assert "replay" in result.output
