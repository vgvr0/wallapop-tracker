"""Persistent PostgreSQL/SQLAlchemy event bus with leases, retries and DLQ."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wallapop_tracker.observability import get_metrics, log_event
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    DeadLetterRecord,
    DomainEventRecord,
    EventConsumptionRecord,
    EventConsumptionStatus,
)

logger = logging.getLogger(__name__)

EVENT_TYPES = (
    "ListingObserved",
    "ListingCreated",
    "ListingRemoved",
    "ListingReappeared",
    "PriceChanged",
    "TitleChanged",
    "ReservationChanged",
    "ShippingChanged",
    "BrandChanged",
    "ConditionChanged",
    "TargetPriceReached",
    "PercentageDropReached",
    "HistoricalLowReached",
    "DealScoreThresholdCrossed",
    "PossibleRelistingDetected",
    "NotificationRequested",
    "NotificationDelivered",
    "NotificationFailed",
)


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(UTC)


class EventBus:
    """Outbox publisher and independent consumer delivery state."""

    def __init__(self, database: Database, *, worker_id: str | None = None) -> None:
        self.database = database
        self.worker_id = worker_id or os.getenv(
            "WALLAPOP_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )

    @staticmethod
    def publish(
        session: Session,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str | int,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        idempotency_key: str,
        marketplace: str | None = None,
    ) -> tuple[DomainEventRecord, bool]:
        """Publish in the caller's transaction; duplicate facts return the original row."""
        existing = session.scalar(
            select(DomainEventRecord).where(DomainEventRecord.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing, False
        at = _now(occurred_at)
        record = DomainEventRecord(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            marketplace=marketplace,
            payload_json=json.dumps(payload or {}, sort_keys=True, default=str),
            metadata_json=json.dumps(metadata or {}, sort_keys=True, default=str),
            occurred_at=at,
            created_at=at,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
        )
        try:
            with session.begin_nested():
                session.add(record)
                session.flush()
        except IntegrityError:
            existing = session.scalar(
                select(DomainEventRecord).where(
                    DomainEventRecord.idempotency_key == idempotency_key
                )
            )
            if existing is None:
                raise
            return existing, False
        get_metrics().event_bus_published_total.labels(event_type).inc()
        log_event(
            logger,
            logging.INFO,
            "event.published",
            event_id=record.id,
            event_type=event_type,
            correlation_id=correlation_id,
        )
        return record, True

    def claim_next(
        self,
        session: Session,
        *,
        consumer: str,
        now: datetime | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 5,
    ) -> tuple[DomainEventRecord, EventConsumptionRecord] | None:
        now = _now(now)
        consumption_join = and_(
            EventConsumptionRecord.event_id == DomainEventRecord.id,
            EventConsumptionRecord.consumer_name == consumer,
        )
        statement = (
            select(DomainEventRecord, EventConsumptionRecord)
            .outerjoin(EventConsumptionRecord, consumption_join)
            .where(
                or_(
                    EventConsumptionRecord.id.is_(None),
                    and_(
                        EventConsumptionRecord.status.in_(
                            [
                                EventConsumptionStatus.PENDING,
                                EventConsumptionStatus.RETRY,
                                EventConsumptionStatus.PROCESSING,
                            ]
                        ),
                        or_(
                            EventConsumptionRecord.next_attempt_at.is_(None),
                            EventConsumptionRecord.next_attempt_at <= now,
                        ),
                        or_(
                            EventConsumptionRecord.status != EventConsumptionStatus.PROCESSING,
                            EventConsumptionRecord.lease_expires_at.is_(None),
                            EventConsumptionRecord.lease_expires_at <= now,
                        ),
                        EventConsumptionRecord.attempts < max_attempts,
                    ),
                )
            )
            .order_by(DomainEventRecord.created_at, DomainEventRecord.id)
            .limit(1)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = session.execute(statement).first()
        if row is None:
            return None
        event, consumption = row
        if consumption is None:
            consumption = EventConsumptionRecord(
                event_id=event.id,
                consumer_name=consumer,
                status=EventConsumptionStatus.PENDING,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(consumption)
                    session.flush()
            except IntegrityError:
                consumption = session.scalar(
                    select(EventConsumptionRecord).where(
                        EventConsumptionRecord.event_id == event.id,
                        EventConsumptionRecord.consumer_name == consumer,
                    )
                )
                if consumption is None:
                    raise
        consumption.status = EventConsumptionStatus.PROCESSING
        consumption.attempts += 1
        consumption.claimed_by = self.worker_id
        consumption.lease_expires_at = now + timedelta(seconds=max(1, lease_seconds))
        consumption.next_attempt_at = None
        consumption.updated_at = now
        session.flush()
        get_metrics().event_bus_claimed_total.labels(consumer).inc()
        log_event(
            logger,
            logging.INFO,
            "event.claimed",
            event_id=event.id,
            event_type=event.event_type,
            consumer=consumer,
            correlation_id=event.correlation_id,
            attempt=consumption.attempts,
        )
        return event, consumption

    def complete(self, session: Session, consumption_id: int, *, consumer: str) -> None:
        row = session.get(EventConsumptionRecord, consumption_id)
        if row is None or row.claimed_by != self.worker_id:
            return
        now = datetime.now(UTC)
        row.status = EventConsumptionStatus.PROCESSED
        row.processed_at = now
        row.claimed_by = None
        row.lease_expires_at = None
        row.updated_at = now
        get_metrics().event_bus_processed_total.labels(consumer, "processed").inc()
        log_event(
            logger,
            logging.INFO,
            "event.processed",
            event_id=row.event_id,
            consumer=consumer,
            status="processed",
        )

    def fail(
        self,
        session: Session,
        consumption_id: int,
        *,
        consumer: str,
        error: str,
        max_attempts: int = 5,
        backoff_base_seconds: int = 30,
    ) -> bool:
        row = session.get(EventConsumptionRecord, consumption_id)
        if row is None or row.claimed_by != self.worker_id:
            return False
        now = datetime.now(UTC)
        row.last_error = error[:4000]
        get_metrics().event_bus_processing_failures_total.labels(consumer).inc()
        row.claimed_by = None
        row.lease_expires_at = None
        row.updated_at = now
        event = session.get(DomainEventRecord, row.event_id)
        if row.attempts >= max_attempts:
            row.status = EventConsumptionStatus.DLQ
            session.add(
                DeadLetterRecord(
                    event_id=row.event_id,
                    consumer_name=consumer,
                    attempts=row.attempts,
                    last_error=row.last_error,
                    correlation_id=event.correlation_id if event else None,
                    failed_at=now,
                )
            )
            get_metrics().event_bus_dlq_total.labels(consumer).inc()
            log_event(
                logger,
                logging.ERROR,
                "event.dead_lettered",
                event_id=row.event_id,
                event_type=event.event_type if event else None,
                consumer=consumer,
                correlation_id=event.correlation_id if event else None,
            )
            return True
        delay = min(3600, backoff_base_seconds * (2 ** max(0, row.attempts - 1)))
        row.status = EventConsumptionStatus.RETRY
        row.next_attempt_at = now + timedelta(seconds=delay)
        get_metrics().event_bus_retries_total.labels(consumer).inc()
        log_event(
            logger,
            logging.WARNING,
            "event.retry_scheduled",
            event_id=row.event_id,
            event_type=event.event_type if event else None,
            consumer=consumer,
            attempt=row.attempts,
        )
        return False

    def requeue(self, session: Session, dead_letter_id: int) -> EventConsumptionRecord:
        dead = session.get(DeadLetterRecord, dead_letter_id)
        if dead is None:
            raise ValueError(f"Unknown dead letter: {dead_letter_id}")
        consumption = session.scalar(
            select(EventConsumptionRecord).where(
                EventConsumptionRecord.event_id == dead.event_id,
                EventConsumptionRecord.consumer_name == dead.consumer_name,
            )
        )
        if consumption is None:
            raise ValueError("Dead letter has no consumption state")
        now = datetime.now(UTC)
        consumption.status = EventConsumptionStatus.PENDING
        consumption.attempts = 0
        consumption.next_attempt_at = now
        consumption.last_error = None
        consumption.replay_count += 1
        consumption.updated_at = now
        dead.requeued_at = now
        dead.requeue_count += 1
        log_event(
            logger,
            logging.INFO,
            "event.requeued",
            event_id=dead.event_id,
            consumer=dead.consumer_name,
            dead_letter_id=dead.id,
        )
        return consumption

    def consume_once(
        self,
        consumer: str,
        handler: Callable[[DomainEventRecord], None],
        *,
        lease_seconds: int = 300,
        max_attempts: int = 5,
        backoff_base_seconds: int = 30,
    ) -> bool:
        with self.database.transaction() as session:
            claimed = self.claim_next(
                session, consumer=consumer, lease_seconds=lease_seconds, max_attempts=max_attempts
            )
            if claimed is None:
                return False
            event, consumption = claimed
            consumption_id = consumption.id
        try:
            started = time.perf_counter()
            handler(event)
        except Exception as exc:
            with self.database.transaction() as session:
                self.fail(
                    session,
                    consumption_id,
                    consumer=consumer,
                    error=type(exc).__name__ + ": " + str(exc),
                    max_attempts=max_attempts,
                    backoff_base_seconds=backoff_base_seconds,
                )
            return True
        get_metrics().event_bus_processing_duration_seconds.labels(consumer).observe(
            time.perf_counter() - started
        )
        with self.database.transaction() as session:
            self.complete(session, consumption_id, consumer=consumer)
        return True

    def list_events(
        self,
        session: Session,
        *,
        event_type: str | None = None,
        consumer: str | None = None,
        status: str | None = None,
        limit: int = 100,
        since: datetime | None = None,
    ) -> list[DomainEventRecord]:
        statement = select(DomainEventRecord).order_by(DomainEventRecord.id).limit(limit)
        if event_type:
            statement = statement.where(DomainEventRecord.event_type == event_type)
        if since:
            statement = statement.where(DomainEventRecord.created_at >= since)
        if consumer or status:
            statement = statement.join(
                EventConsumptionRecord, EventConsumptionRecord.event_id == DomainEventRecord.id
            )
            if consumer:
                statement = statement.where(EventConsumptionRecord.consumer_name == consumer)
            if status:
                statement = statement.where(EventConsumptionRecord.status == status)
        return list(session.scalars(statement))

    def replay(
        self,
        session: Session,
        *,
        consumer: str,
        from_id: int,
        to_id: int,
        allow_side_effects: bool = False,
    ) -> int:
        if consumer != "analytics" and not allow_side_effects:
            raise ValueError(
                "Only read-only analytics replay is allowed without --allow-side-effects"
            )
        now = datetime.now(UTC)
        events = session.scalars(
            select(DomainEventRecord).where(
                DomainEventRecord.id >= from_id, DomainEventRecord.id <= to_id
            )
        ).all()
        count = 0
        for event in events:
            row = session.scalar(
                select(EventConsumptionRecord).where(
                    EventConsumptionRecord.event_id == event.id,
                    EventConsumptionRecord.consumer_name == consumer,
                )
            )
            if row is None:
                row = EventConsumptionRecord(
                    event_id=event.id,
                    consumer_name=consumer,
                    status=EventConsumptionStatus.PENDING,
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            elif row.status in {EventConsumptionStatus.PROCESSED, EventConsumptionStatus.DLQ}:
                row.status = EventConsumptionStatus.PENDING
                row.next_attempt_at = now
                row.replay_count += 1
                row.updated_at = now
            count += 1
        return count


def deserialize_event(event: DomainEventRecord) -> dict[str, Any]:
    """Return a stable JSON-compatible event envelope for consumers and APIs."""
    return {
        "id": event.id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "marketplace": event.marketplace,
        "payload": json.loads(event.payload_json),
        "metadata": json.loads(event.metadata_json),
        "occurred_at": event.occurred_at.isoformat(),
        "created_at": event.created_at.isoformat(),
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
    }
