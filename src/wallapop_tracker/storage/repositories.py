"""Repositories for isolated historical persistence operations."""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wallapop_tracker.domain.marketplace import Marketplace, require_supported_marketplace
from wallapop_tracker.exceptions import WallapopError
from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary
from wallapop_tracker.observability import get_metrics

from .models import (
    DealScoreSnapshotRecord,
    ListingRecord,
    ListingSnapshotRecord,
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    PossibleRelistingRecord,
    PossibleRelistingStatus,
    PresenceState,
    ProfileRecord,
    ProfileSnapshotRecord,
    SearchListingMatchRecord,
    TrackedListingRecord,
    TrackedProfileRecord,
    TrackedSearchRecord,
    TrackingEventRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


def latest_deal_score(
    session: Session, listing_id: int, search_id: int
) -> DealScoreSnapshotRecord | None:
    return session.scalar(
        select(DealScoreSnapshotRecord)
        .where(
            DealScoreSnapshotRecord.listing_id == listing_id,
            DealScoreSnapshotRecord.tracked_search_id == search_id,
        )
        .order_by(DealScoreSnapshotRecord.computed_at.desc(), DealScoreSnapshotRecord.id.desc())
        .limit(1)
    )


def _validate_alert_thresholds(
    target_price: Decimal | None,
    percentage_drop_threshold: Decimal | None,
    deal_score_threshold: Decimal | None,
) -> None:
    if target_price is not None and target_price < 0:
        raise ValueError("target_price must be non-negative")
    if percentage_drop_threshold is not None and not 0 < percentage_drop_threshold <= 100:
        raise ValueError("percentage_drop_threshold must be between 0 and 100")
    if deal_score_threshold is not None and not 0 <= deal_score_threshold <= 100:
        raise ValueError("deal_score_threshold must be between 0 and 100")


class TrackedSearchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, search_id: int) -> TrackedSearchRecord | None:
        return self.session.get(TrackedSearchRecord, search_id)

    def list_all(self) -> list[TrackedSearchRecord]:
        return list(
            self.session.scalars(select(TrackedSearchRecord).order_by(TrackedSearchRecord.id))
        )

    def list_enabled(self) -> list[TrackedSearchRecord]:
        return list(
            self.session.scalars(
                select(TrackedSearchRecord)
                .where(TrackedSearchRecord.enabled)
                .order_by(TrackedSearchRecord.id)
            )
        )

    def create(
        self,
        query: str,
        *,
        name: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        filters: dict[str, Any] | None = None,
        interval_seconds: int = 600,
        notify_on_first_run: bool = False,
        marketplace: Marketplace = Marketplace.WALLAPOP,
        target_price: Decimal | None = None,
        percentage_drop_threshold: Decimal | None = None,
        deal_score_threshold: Decimal | None = None,
        notify_on_30d_low: bool = False,
        notify_on_90d_low: bool = False,
        notify_on_all_time_low: bool = False,
    ) -> TrackedSearchRecord:
        query = query.strip()
        if not query:
            raise ValueError("query is required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise ValueError("min_price must not exceed max_price")
        _validate_search_filters(filters)
        _validate_alert_thresholds(target_price, percentage_drop_threshold, deal_score_threshold)
        now = datetime.now(UTC)
        record = TrackedSearchRecord(
            marketplace=require_supported_marketplace(marketplace).value,
            name=name.strip() if name and name.strip() else None,
            query=query,
            min_price=min_price,
            max_price=max_price,
            filters_json=_json_text(filters),
            interval_seconds=interval_seconds,
            notify_on_first_run=notify_on_first_run,
            target_price=target_price,
            percentage_drop_threshold=percentage_drop_threshold,
            deal_score_threshold=deal_score_threshold,
            notify_on_30d_low=notify_on_30d_low,
            notify_on_90d_low=notify_on_90d_low,
            notify_on_all_time_low=notify_on_all_time_low,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def has_valid_run(self, search_id: int) -> bool:
        return (
            self.session.scalar(
                select(TrackingRunRecord.id)
                .where(
                    TrackingRunRecord.tracked_search_id == search_id,
                    TrackingRunRecord.status == TrackingRunStatus.VALID,
                )
                .limit(1)
            )
            is not None
        )

    def enable(self, search_id: int) -> TrackedSearchRecord:
        return self._set_enabled(search_id, True)

    def disable(self, search_id: int) -> TrackedSearchRecord:
        return self._set_enabled(search_id, False)

    def _set_enabled(self, search_id: int, enabled: bool) -> TrackedSearchRecord:
        record = self.get(search_id)
        if record is None:
            raise ValueError(f"Unknown search: {search_id}")
        record.enabled = enabled
        record.updated_at = datetime.now(UTC)
        self.session.flush()
        return record

    def remove(self, search_id: int) -> None:
        record = self.get(search_id)
        if record is None:
            raise ValueError(f"Unknown search: {search_id}")
        for run in self.session.scalars(
            select(TrackingRunRecord).where(TrackingRunRecord.tracked_search_id == search_id)
        ):
            run.tracked_search_id = None
        for event in self.session.scalars(
            select(TrackingEventRecord).where(TrackingEventRecord.tracked_search_id == search_id)
        ):
            event.tracked_search_id = None
        self.session.query(SearchListingMatchRecord).filter(
            SearchListingMatchRecord.tracked_search_id == search_id
        ).delete(synchronize_session=False)
        self.session.delete(record)
        self.session.flush()

    def update_last_run(
        self,
        search_id: int,
        at: datetime,
        status: str,
        run_id: int | None = None,
    ) -> TrackedSearchRecord:
        record = self.get(search_id)
        if record is None:
            raise ValueError(f"Unknown search: {search_id}")
        record.last_run_at = at
        record.last_run_status = status
        record.last_run_id = run_id
        record.updated_at = datetime.now(UTC)
        self.session.flush()
        return record

    def update(
        self,
        search_id: int,
        *,
        name: str | None = None,
        query: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        filters: dict[str, Any] | None = None,
        enabled: bool | None = None,
        notify_on_first_run: bool | None = None,
        interval_seconds: int | None = None,
        marketplace: str | Marketplace | None = None,
        target_price: Decimal | None = None,
        percentage_drop_threshold: Decimal | None = None,
        deal_score_threshold: Decimal | None = None,
        notify_on_30d_low: bool | None = None,
        notify_on_90d_low: bool | None = None,
        notify_on_all_time_low: bool | None = None,
    ) -> TrackedSearchRecord:
        record = self.get(search_id)
        if record is None:
            raise ValueError(f"Unknown search: {search_id}")
        if query is not None and not query.strip():
            raise ValueError("query is required")
        if interval_seconds is not None and interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        effective_min = min_price if min_price is not None else record.min_price
        effective_max = max_price if max_price is not None else record.max_price
        if (
            effective_min is not None
            and effective_max is not None
            and effective_min > effective_max
        ):
            raise ValueError("min_price must not exceed max_price")
        if name is not None:
            record.name = name.strip() or None
        if query is not None:
            record.query = query.strip()
        if min_price is not None:
            record.min_price = min_price
        if max_price is not None:
            record.max_price = max_price
        if filters is not None:
            _validate_search_filters(filters)
            record.filters_json = _json_text(filters)
        if enabled is not None:
            record.enabled = enabled
        if notify_on_first_run is not None:
            record.notify_on_first_run = notify_on_first_run
        if interval_seconds is not None:
            record.interval_seconds = interval_seconds
        if marketplace is not None:
            record.marketplace = require_supported_marketplace(marketplace).value
        _validate_alert_thresholds(target_price, percentage_drop_threshold, deal_score_threshold)
        for name, value in (
            ("target_price", target_price),
            ("percentage_drop_threshold", percentage_drop_threshold),
            ("deal_score_threshold", deal_score_threshold),
            ("notify_on_30d_low", notify_on_30d_low),
            ("notify_on_90d_low", notify_on_90d_low),
            ("notify_on_all_time_low", notify_on_all_time_low),
        ):
            if value is not None:
                setattr(record, name, value)
        record.updated_at = _utc_now()
        self.session.flush()
        return record


class TrackedListingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, listing_id: int) -> TrackedListingRecord | None:
        return self.session.get(TrackedListingRecord, listing_id)

    def get_by_alias_or_id(self, value: str) -> TrackedListingRecord | None:
        value = value.strip()
        if value.isdigit():
            record = self.get(int(value))
            if record is not None:
                return record
        return self.session.scalar(
            select(TrackedListingRecord).where(TrackedListingRecord.alias == value)
        )

    def list_all(self) -> list[TrackedListingRecord]:
        return list(
            self.session.scalars(select(TrackedListingRecord).order_by(TrackedListingRecord.id))
        )

    def list_enabled(self) -> list[TrackedListingRecord]:
        return list(
            self.session.scalars(
                select(TrackedListingRecord)
                .where(TrackedListingRecord.enabled)
                .order_by(TrackedListingRecord.id)
            )
        )

    def create(
        self,
        listing_id: int,
        alias: str,
        *,
        interval_seconds: int = 600,
        notes: str | None = None,
        target_price: Decimal | None = None,
        percentage_drop_threshold: Decimal | None = None,
        deal_score_threshold: Decimal | None = None,
        notify_on_30d_low: bool = False,
        notify_on_90d_low: bool = False,
        notify_on_all_time_low: bool = False,
    ) -> TrackedListingRecord:
        alias = alias.strip()
        if not alias:
            raise ValueError("alias is required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        _validate_alert_thresholds(target_price, percentage_drop_threshold, deal_score_threshold)
        if self.session.get(ListingRecord, listing_id) is None:
            raise ValueError(f"Unknown listing: {listing_id}")
        now = _utc_now()
        record = TrackedListingRecord(
            listing_id=listing_id,
            alias=alias,
            interval_seconds=interval_seconds,
            notes=notes,
            target_price=target_price,
            percentage_drop_threshold=percentage_drop_threshold,
            deal_score_threshold=deal_score_threshold,
            notify_on_30d_low=notify_on_30d_low,
            notify_on_90d_low=notify_on_90d_low,
            notify_on_all_time_low=notify_on_all_time_low,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def set_enabled(self, value: str, enabled: bool) -> TrackedListingRecord:
        record = self.get_by_alias_or_id(value)
        if record is None:
            raise ValueError(f"Unknown tracked listing: {value}")
        record.enabled = enabled
        record.updated_at = _utc_now()
        self.session.flush()
        return record

    def update_last_run(
        self,
        listing_id: int,
        at: datetime,
        status: str,
        run_id: int | None,
    ) -> TrackedListingRecord:
        record = self.get(listing_id)
        if record is None:
            raise ValueError(f"Unknown tracked listing: {listing_id}")
        record.last_run_at = at
        record.last_run_status = status
        record.last_tracking_run_id = run_id
        record.updated_at = _utc_now()
        self.session.flush()
        return record

    def update(
        self,
        listing_id: int,
        *,
        enabled: bool | None = None,
        interval_seconds: int | None = None,
        notes: str | None = None,
        target_price: Decimal | None = None,
        percentage_drop_threshold: Decimal | None = None,
        deal_score_threshold: Decimal | None = None,
        notify_on_30d_low: bool | None = None,
        notify_on_90d_low: bool | None = None,
        notify_on_all_time_low: bool | None = None,
    ) -> TrackedListingRecord:
        record = self.get(listing_id)
        if record is None:
            raise ValueError(f"Unknown tracked listing: {listing_id}")
        if interval_seconds is not None and interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if enabled is not None:
            record.enabled = enabled
        if interval_seconds is not None:
            record.interval_seconds = interval_seconds
        if notes is not None:
            record.notes = notes
        _validate_alert_thresholds(target_price, percentage_drop_threshold, deal_score_threshold)
        for name, value in (
            ("target_price", target_price),
            ("percentage_drop_threshold", percentage_drop_threshold),
            ("deal_score_threshold", deal_score_threshold),
            ("notify_on_30d_low", notify_on_30d_low),
            ("notify_on_90d_low", notify_on_90d_low),
            ("notify_on_all_time_low", notify_on_all_time_low),
        ):
            if value is not None:
                setattr(record, name, value)
        record.updated_at = _utc_now()
        self.session.flush()
        return record

    def remove(self, value: str) -> None:
        record = self.get_by_alias_or_id(value)
        if record is None:
            raise ValueError(f"Unknown tracked listing: {value}")
        if (
            self.session.scalar(
                select(TrackingRunRecord.id)
                .where(TrackingRunRecord.tracked_listing_id == record.id)
                .limit(1)
            )
            is not None
        ):
            raise ValueError(
                "Tracked listing has historical runs; disable it instead of removing it"
            )
        self.session.delete(record)
        self.session.flush()


class SearchMatchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def touch(
        self, search_id: int, listing_id: int, observed_at: datetime
    ) -> SearchListingMatchRecord:
        record = self.session.get(SearchListingMatchRecord, (search_id, listing_id))
        if record is None:
            record = SearchListingMatchRecord(
                tracked_search_id=search_id,
                listing_id=listing_id,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                detection_count=1,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(record)
                    self.session.flush()
            except IntegrityError:
                existing = self.session.get(SearchListingMatchRecord, (search_id, listing_id))
                if existing is None:
                    raise
                record = existing
            else:
                return record
        else:
            record.last_seen_at = observed_at
            record.detection_count += 1
        self.session.flush()
        return record

    def get(self, search_id: int, listing_id: int) -> SearchListingMatchRecord | None:
        return self.session.get(SearchListingMatchRecord, (search_id, listing_id))


class TrackingEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_key(self, key: str) -> TrackingEventRecord | None:
        return self.session.scalar(
            select(TrackingEventRecord).where(TrackingEventRecord.idempotency_key == key)
        )

    def create_once(
        self,
        *,
        event_type: str,
        idempotency_key: str,
        listing_id: int,
        tracking_run_id: int,
        tracked_search_id: int | None,
        old_price: Decimal | None,
        new_price: Decimal | None,
        created_at: datetime,
        metadata_json: str | None = None,
    ) -> tuple[TrackingEventRecord, bool]:
        existing = self.get_by_key(idempotency_key)
        if existing is not None:
            return existing, False
        try:
            with self.session.begin_nested():
                record = TrackingEventRecord(
                    event_type=event_type,
                    idempotency_key=idempotency_key,
                    listing_id=listing_id,
                    tracking_run_id=tracking_run_id,
                    tracked_search_id=tracked_search_id,
                    old_price=old_price,
                    new_price=new_price,
                    created_at=created_at,
                    metadata_json=metadata_json,
                )
                self.session.add(record)
                self.session.flush()
        except IntegrityError:
            existing = self.get_by_key(idempotency_key)
            if existing is None:
                raise
            return existing, False
        get_metrics().tracking_events_created_total.labels(event_type).inc()
        return record, True


class NotificationDeliveryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[NotificationDeliveryRecord]:
        return list(
            self.session.scalars(
                select(NotificationDeliveryRecord).order_by(NotificationDeliveryRecord.id)
            )
        )

    def create_once(
        self,
        *,
        event_id: int,
        channel: str,
        destination: str,
        created_at: datetime,
    ) -> tuple[NotificationDeliveryRecord, bool]:
        existing = self.session.scalar(
            select(NotificationDeliveryRecord).where(
                NotificationDeliveryRecord.event_id == event_id,
                NotificationDeliveryRecord.channel == channel,
                NotificationDeliveryRecord.destination == destination,
            )
        )
        if existing is not None:
            return existing, False
        record = NotificationDeliveryRecord(
            event_id=event_id,
            channel=channel,
            destination=destination,
            status=NotificationDeliveryStatus.PENDING,
            attempts=0,
            created_at=created_at,
            updated_at=created_at,
        )
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(NotificationDeliveryRecord).where(
                    NotificationDeliveryRecord.event_id == event_id,
                    NotificationDeliveryRecord.channel == channel,
                    NotificationDeliveryRecord.destination == destination,
                )
            )
            if existing is None:
                raise
            return existing, False
        return record, True

    def next_pending(
        self, max_attempts: int, *, include_failed: bool = False
    ) -> NotificationDeliveryRecord | None:
        statuses = [NotificationDeliveryStatus.PENDING]
        if include_failed:
            statuses.append(NotificationDeliveryStatus.FAILED)
        return self.session.scalar(
            select(NotificationDeliveryRecord)
            .where(
                NotificationDeliveryRecord.status.in_(statuses),
                NotificationDeliveryRecord.attempts < max_attempts,
            )
            .order_by(NotificationDeliveryRecord.created_at, NotificationDeliveryRecord.id)
            .limit(1)
        )

    def claim_next(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int,
        include_failed: bool = False,
    ) -> NotificationDeliveryRecord | None:
        """Claim one delivery in a short transaction; PostgreSQL skips locked rows."""
        statuses = [NotificationDeliveryStatus.PENDING, NotificationDeliveryStatus.PROCESSING]
        if include_failed:
            statuses.append(NotificationDeliveryStatus.FAILED)
        query = (
            select(NotificationDeliveryRecord)
            .where(
                NotificationDeliveryRecord.status.in_(statuses),
                NotificationDeliveryRecord.attempts < max_attempts,
                (
                    NotificationDeliveryRecord.status != NotificationDeliveryStatus.PROCESSING
                )
                | (NotificationDeliveryRecord.claim_expires_at.is_(None))
                | (NotificationDeliveryRecord.claim_expires_at <= now),
                *(
                    ()
                    if include_failed
                    else (
                        (NotificationDeliveryRecord.next_attempt_at.is_(None))
                        | (NotificationDeliveryRecord.next_attempt_at <= now),
                    )
                ),
            )
            .order_by(NotificationDeliveryRecord.created_at, NotificationDeliveryRecord.id)
            .limit(1)
        )
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        record = self.session.scalar(query)
        if record is None:
            return None
        record.status = NotificationDeliveryStatus.PROCESSING
        record.claimed_by = worker_id
        record.processing_started_at = now
        record.claim_expires_at = now + timedelta(seconds=lease_seconds)
        record.updated_at = now
        self.session.flush()
        return record


def claim_tracking_jobs(
    session: Session,
    *,
    now: datetime,
    worker_id: str,
    lease_seconds: int,
    interval: timedelta,
) -> tuple[list[TrackedProfileRecord], list[TrackedSearchRecord], list[TrackedListingRecord]]:
    """Claim due tracking sources without holding a DB lock during HTTP I/O."""
    claimed: list[list[Any]] = [[], [], []]
    definitions = (
        (TrackedProfileRecord, TrackedProfileRepository, interval),
        (TrackedSearchRecord, TrackedSearchRepository, None),
        (TrackedListingRecord, TrackedListingRepository, None),
    )
    for index, (model, _repository, fixed_interval) in enumerate(definitions):
        # Read candidate ids without locks, then lock one candidate at a time.
        # This prevents a worker from locking the complete table while it scans
        # due state and lets another worker make progress on other rows.
        candidate_ids = session.scalars(
            select(model.id).where(model.enabled).order_by(model.id)
        ).all()
        for candidate_id in candidate_ids:
            query = select(model).where(model.id == candidate_id)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            record = session.scalar(query)
            if record is None:
                continue
            due_interval = fixed_interval or timedelta(seconds=record.interval_seconds)
            last = (
                record.last_run_at.astimezone(UTC)
                if record.last_run_at and record.last_run_at.tzinfo
                else record.last_run_at.replace(tzinfo=UTC)
                if record.last_run_at
                else None
            )
            available = record.claim_expires_at is None or record.claim_expires_at <= now
            if available and (last is None or last + due_interval <= now):
                record.claimed_at = now
                record.claim_expires_at = now + timedelta(seconds=lease_seconds)
                record.claimed_by = worker_id
                claimed[index].append(record)
        session.flush()
    return claimed[0], claimed[1], claimed[2]


def release_tracking_claim(session: Session, model: Any, record_id: int, worker_id: str) -> None:
    record = session.get(model, record_id)
    if record is not None and record.claimed_by == worker_id:
        record.claimed_at = None
        record.claim_expires_at = None
        record.claimed_by = None


class TrackedProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_alias(self, alias: str) -> TrackedProfileRecord | None:
        return self.session.scalar(
            select(TrackedProfileRecord).where(TrackedProfileRecord.alias == alias.strip().lower())
        )

    def get_by_user_id(self, user_id: str) -> TrackedProfileRecord | None:
        return self.session.scalar(
            select(TrackedProfileRecord).where(TrackedProfileRecord.wallapop_user_id == user_id)
        )

    def get_by_url(self, url: str) -> TrackedProfileRecord | None:
        return self.session.scalar(
            select(TrackedProfileRecord).where(TrackedProfileRecord.profile_url == url)
        )

    def list_all(self) -> list[TrackedProfileRecord]:
        return list(
            self.session.scalars(select(TrackedProfileRecord).order_by(TrackedProfileRecord.alias))
        )

    def list_enabled(self) -> list[TrackedProfileRecord]:
        return list(
            self.session.scalars(
                select(TrackedProfileRecord)
                .where(TrackedProfileRecord.enabled)
                .order_by(TrackedProfileRecord.alias)
            )
        )

    def create(
        self, profile_url: str, user_id: str, alias: str, notes: str | None = None
    ) -> TrackedProfileRecord:
        normalized = alias.strip().lower()
        if not normalized:
            raise ValueError("alias is required")
        if self.get_by_alias(normalized):
            raise ValueError(f"Alias already exists: {normalized}")
        if self.get_by_url(profile_url):
            raise ValueError(f"Profile URL already exists: {profile_url}")
        if self.get_by_user_id(user_id):
            raise ValueError(f"Wallapop user ID already exists: {user_id}")
        record = TrackedProfileRecord(
            profile_url=profile_url,
            wallapop_user_id=user_id,
            alias=normalized,
            notes=notes,
            added_at=datetime.now(UTC),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def enable(self, alias: str) -> TrackedProfileRecord:
        return self._set_enabled(alias, True)

    def disable(self, alias: str) -> TrackedProfileRecord:
        return self._set_enabled(alias, False)

    def _set_enabled(self, alias: str, enabled: bool) -> TrackedProfileRecord:
        record = self.get_by_alias(alias)
        if record is None:
            raise ValueError(f"Unknown alias: {alias}")
        record.enabled = enabled
        self.session.flush()
        return record

    def remove(self, alias: str) -> None:
        record = self.get_by_alias(alias)
        if record is None:
            raise ValueError(f"Unknown alias: {alias}")
        self.session.delete(record)
        self.session.flush()

    def update_last_run(
        self, alias: str, at: datetime, status: TrackingRunStatus
    ) -> TrackedProfileRecord:
        record = self.get_by_alias(alias)
        if record is None:
            raise ValueError(f"Unknown alias: {alias}")
        record.last_run_at, record.last_run_status = at, status.value
        self.session.flush()
        return record

    def attach_profile(self, alias: str, profile_id: int) -> TrackedProfileRecord:
        record = self.get_by_alias(alias)
        if record is None:
            raise ValueError(f"Unknown alias: {alias}")
        record.profile_id = profile_id
        self.session.flush()
        return record


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_search_filters(filters: dict[str, Any] | None) -> None:
    if filters is None:
        return
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
        if key in filters and filters[key] is not None and not low <= float(filters[key]) <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    if (
        "max_distance_km" in filters
        and filters["max_distance_km"] is not None
        and float(filters["max_distance_km"]) <= 0
    ):
        raise ValueError("max_distance_km must be positive")
    for key in ("brands", "brand_ids", "models", "model_ids", "conditions", "condition"):
        value = filters.get(key)
        if isinstance(value, list) and len(value) != len(set(value)):
            raise ValueError(f"{key} must not contain duplicates")
    for key in ("title_include_mode", "description_include_mode"):
        value = filters.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or value.casefold() not in {"any", "all"}:
            raise ValueError(f"{key} must be 'any' or 'all'")
    for key in _TEXT_FILTER_KEYS:
        value = filters.get(key)
        if value is None or isinstance(value, str):
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be a string or a list of strings")


_TEXT_FILTER_KEYS = (
    "title_include",
    "title_must_include",
    "description_include",
    "description_must_include",
    "title_exclude",
    "description_exclude",
    "title_first_word_include",
    "title_first_word_exclude",
)


def _json_text(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else None


def _run_is_valid(session: Session, run_id: int) -> bool:
    run = session.get(TrackingRunRecord, run_id)
    if run is None:
        raise WallapopError(f"Tracking run not found: {run_id}")
    return run.status == TrackingRunStatus.VALID


class ProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_profile_by_wallapop_id(self, user_id: str) -> ProfileRecord | None:
        return self.session.scalar(
            select(ProfileRecord).where(ProfileRecord.wallapop_user_id == user_id)
        )

    def get_or_create_profile(
        self,
        profile: Profile,
        *,
        observed_at: datetime | None = None,
        tracking_run_id: int | None = None,
    ) -> ProfileRecord:
        now = observed_at or _utc_now()
        valid_observation = tracking_run_id is not None and _run_is_valid(
            self.session, tracking_run_id
        )
        record = self.get_profile_by_wallapop_id(profile.user_id)
        if record is None:
            if tracking_run_id is not None and not valid_observation:
                raise WallapopError("A non-valid tracking run cannot establish profile presence")
            record = ProfileRecord(
                wallapop_user_id=profile.user_id,
                slug=profile.slug,
                name=profile.name,
                url=profile.url,
                registered_at=profile.registered_at,
                location_city=profile.location_city,
                postal_code=profile.postal_code,
                country_code=profile.country_code,
                seller_type=profile.seller_type,
                verified=profile.verified,
                is_top_profile=profile.is_top_profile,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            self.session.add(record)
            self.session.flush()
            return record
        record.slug = profile.slug
        record.name = profile.name
        record.url = profile.url
        record.registered_at = profile.registered_at
        record.location_city = profile.location_city
        record.postal_code = profile.postal_code
        record.country_code = profile.country_code
        record.seller_type = profile.seller_type
        record.verified = profile.verified
        record.is_top_profile = profile.is_top_profile
        if valid_observation:
            record.last_seen_at = now
        record.updated_at = now
        self.session.flush()
        return record


class ListingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_listing_by_wallapop_id(self, item_id: str) -> ListingRecord | None:
        return self.get_listing(Marketplace.WALLAPOP, item_id)

    def get_listing(self, marketplace: Marketplace | str, external_id: str) -> ListingRecord | None:
        supported = require_supported_marketplace(marketplace)
        return self.session.scalar(
            select(ListingRecord).where(
                ListingRecord.marketplace == supported.value,
                ListingRecord.external_id == external_id,
            )
        )

    def get_or_create_listing(
        self,
        listing: Listing,
        profile_id: int,
        *,
        observed_at: datetime | None = None,
        tracking_run_id: int | None = None,
    ) -> ListingRecord:
        now = observed_at or _utc_now()
        valid_observation = tracking_run_id is not None and _run_is_valid(
            self.session, tracking_run_id
        )
        record = self.get_listing(listing.marketplace, listing.item_id)
        if record is None:
            if tracking_run_id is not None and not valid_observation:
                raise WallapopError("A non-valid tracking run cannot establish listing presence")
            record = ListingRecord(
                marketplace=listing.marketplace.value,
                wallapop_item_id=listing.item_id,
                external_id=listing.item_id,
                profile_id=profile_id,
                seller_user_id=listing.user_id or None,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            self.session.add(record)
            self.session.flush()
            return record
        if record.profile_id not in {None, profile_id}:
            raise WallapopError(
                f"Listing {listing.item_id} changed profile from "
                f"{record.profile_id} to {profile_id}"
            )
        if valid_observation:
            if record.profile_id is None:
                record.profile_id = profile_id
            if record.seller_user_id is None and listing.user_id:
                record.seller_user_id = listing.user_id
            record.last_seen_at = now
            record.updated_at = now
            record.condition_code = listing.condition_code
            record.condition_label = listing.condition_label
        self.session.flush()
        return record

    def get_or_create_global_listing(
        self,
        listing: Listing,
        profile_id: int | None,
        *,
        observed_at: datetime | None = None,
        tracking_run_id: int | None = None,
    ) -> tuple[ListingRecord, bool]:
        """Upsert by Wallapop ID without changing the original seller anchor.

        Profile tracking historically treats a listing/profile mismatch as an
        invariant violation. Search results are global and do not require a
        profile identity; a later profile capture may attach one to an
        unanchored listing, while an existing seller anchor is preserved.
        """
        now = observed_at or _utc_now()
        valid_observation = tracking_run_id is not None and _run_is_valid(
            self.session, tracking_run_id
        )
        record = self.get_listing(listing.marketplace, listing.item_id)
        created = record is None
        if record is None:
            if tracking_run_id is not None and not valid_observation:
                raise WallapopError("A non-valid tracking run cannot establish listing presence")
            record = ListingRecord(
                marketplace=listing.marketplace.value,
                wallapop_item_id=listing.item_id,
                external_id=listing.item_id,
                profile_id=profile_id,
                seller_user_id=listing.user_id or None,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
                condition_code=listing.condition_code,
                condition_label=listing.condition_label,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(record)
                    self.session.flush()
            except IntegrityError:
                existing = self.get_listing(listing.marketplace, listing.item_id)
                if existing is None:
                    raise
                record, created = existing, False
        if record.profile_id is None and profile_id is not None:
            record.profile_id = profile_id
        if record.seller_user_id is None and listing.user_id:
            record.seller_user_id = listing.user_id
        if valid_observation and record is not None:
            record.last_seen_at = now
            record.updated_at = now
            record.condition_code = listing.condition_code
            record.condition_label = listing.condition_label
            self.session.flush()
        return record, created


class PossibleRelistingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, relisting_id: int) -> PossibleRelistingRecord | None:
        return self.session.get(PossibleRelistingRecord, relisting_id)

    def get_by_pair(
        self, previous_listing_id: int, current_listing_id: int
    ) -> PossibleRelistingRecord | None:
        return self.session.scalar(
            select(PossibleRelistingRecord).where(
                PossibleRelistingRecord.previous_listing_id == previous_listing_id,
                PossibleRelistingRecord.current_listing_id == current_listing_id,
            )
        )

    def create_once(
        self,
        *,
        previous_listing_id: int,
        current_listing_id: int,
        score: Decimal,
        reasons_json: str,
        detected_at: datetime,
    ) -> tuple[PossibleRelistingRecord, bool]:
        existing = self.get_by_pair(previous_listing_id, current_listing_id)
        if existing is not None:
            return existing, False
        record = PossibleRelistingRecord(
            previous_listing_id=previous_listing_id,
            current_listing_id=current_listing_id,
            score=score,
            reasons_json=reasons_json,
            detected_at=detected_at,
            status=PossibleRelistingStatus.CANDIDATE,
        )
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
        except IntegrityError:
            existing = self.get_by_pair(previous_listing_id, current_listing_id)
            if existing is None:
                raise
            return existing, False
        return record, True

    def list_all(
        self, *, min_score: Decimal | None = None, listing_id: int | None = None
    ) -> list[PossibleRelistingRecord]:
        statement = select(PossibleRelistingRecord).order_by(
            PossibleRelistingRecord.score.desc(), PossibleRelistingRecord.detected_at.desc()
        )
        if min_score is not None:
            statement = statement.where(PossibleRelistingRecord.score >= min_score)
        if listing_id is not None:
            statement = statement.where(
                (PossibleRelistingRecord.previous_listing_id == listing_id)
                | (PossibleRelistingRecord.current_listing_id == listing_id)
            )
        return list(self.session.scalars(statement))


class TrackingRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start_profile_run(
        self,
        profile_id: int,
        *,
        started_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> TrackingRunRecord:
        record = TrackingRunRecord(
            profile_id=profile_id,
            started_at=started_at or _utc_now(),
            status=TrackingRunStatus.RUNNING,
            idempotency_key=idempotency_key,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def start_search_run(
        self,
        tracked_search_id: int,
        *,
        started_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> TrackingRunRecord:
        record = TrackingRunRecord(
            tracked_search_id=tracked_search_id,
            started_at=started_at or _utc_now(),
            status=TrackingRunStatus.RUNNING,
            idempotency_key=idempotency_key,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def start_listing_run(
        self,
        tracked_listing_id: int,
        *,
        started_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> TrackingRunRecord:
        record = TrackingRunRecord(
            tracked_listing_id=tracked_listing_id,
            started_at=started_at or _utc_now(),
            status=TrackingRunStatus.RUNNING,
            idempotency_key=idempotency_key,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def start_tracking_run(
        self,
        profile_id: int,
        *,
        started_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> TrackingRunRecord:
        """Compatibility alias for callers that create profile runs."""
        return self.start_profile_run(
            profile_id, started_at=started_at, idempotency_key=idempotency_key
        )

    def finish_tracking_run(
        self,
        run_id: int,
        *,
        status: str | TrackingRunStatus,
        finished_at: datetime | None = None,
        items_fetched: int | None = None,
        pages_fetched: int | None = None,
        profile_ok: bool = False,
        stats_ok: bool = False,
        reviews_ok: bool = False,
        items_ok: bool = False,
        error_type: str | None = None,
        error_message: str | None = None,
        matched_listings: int | None = None,
        new_listings: int | None = None,
        price_changes: int | None = None,
        duplicates_suppressed: int | None = None,
        health_status: str | None = None,
        duration_ms: int | None = None,
        items_scanned: int | None = None,
        items_new: int | None = None,
        items_changed: int | None = None,
        items_missing: int | None = None,
        items_sold: int | None = None,
        http_requests: int | None = None,
        http_errors: int | None = None,
        http_403: int | None = None,
        http_429: int | None = None,
        http_5xx: int | None = None,
        parse_errors: int | None = None,
        suspicious_result: bool | None = None,
    ) -> TrackingRunRecord:
        if status not in {
            TrackingRunStatus.VALID,
            TrackingRunStatus.PARTIAL,
            TrackingRunStatus.FAILED,
        }:
            raise ValueError("Finished tracking run status must be valid, partial, or failed")
        record = self.session.get(TrackingRunRecord, run_id)
        if record is None:
            raise WallapopError(f"Tracking run not found: {run_id}")
        if record.status != "running":
            raise WallapopError(f"Tracking run is already finished: {run_id}")
        record.status = TrackingRunStatus(status)
        record.finished_at = finished_at or _utc_now()
        record.items_fetched = items_fetched
        record.pages_fetched = pages_fetched
        record.profile_ok = profile_ok
        record.stats_ok = stats_ok
        record.reviews_ok = reviews_ok
        record.items_ok = items_ok
        record.error_type = error_type
        record.error_message = error_message
        record.matched_listings = matched_listings
        record.new_listings = new_listings
        record.price_changes = price_changes
        record.duplicates_suppressed = duplicates_suppressed
        record.health_status = health_status
        record.duration_ms = duration_ms
        record.items_scanned = items_scanned if items_scanned is not None else items_fetched
        record.items_new = items_new if items_new is not None else new_listings
        record.items_changed = items_changed if items_changed is not None else price_changes
        record.items_missing = items_missing
        record.items_sold = items_sold
        record.http_requests = http_requests
        record.http_errors = http_errors
        record.http_403 = http_403
        record.http_429 = http_429
        record.http_5xx = http_5xx
        record.parse_errors = parse_errors
        record.suspicious_result = suspicious_result
        self.session.flush()
        source = (
            "profile"
            if record.profile_id is not None
            else "search"
            if record.tracked_search_id is not None
            else "listing"
        )
        get_metrics().tracking_runs_total.labels(source, record.status.value).inc()
        if record.status == TrackingRunStatus.FAILED:
            get_metrics().tracking_runs_failed_total.labels(source).inc()
        if record.started_at is not None and record.finished_at is not None:
            started = (
                record.started_at.replace(tzinfo=UTC)
                if record.started_at.tzinfo is None
                else record.started_at
            )
            finished = (
                record.finished_at.replace(tzinfo=UTC)
                if record.finished_at.tzinfo is None
                else record.finished_at
            )
            get_metrics().tracking_run_duration_seconds.labels(source).observe(
                max(0.0, (finished - started).total_seconds())
            )
        if items_fetched is not None:
            get_metrics().listings_fetched_total.labels(source).inc(max(0, items_fetched))
        return record

    def mark_valid(self, run_id: int, **kwargs: Any) -> TrackingRunRecord:
        return self.finish_tracking_run(run_id, status=TrackingRunStatus.VALID, **kwargs)

    def mark_partial(self, run_id: int, **kwargs: Any) -> TrackingRunRecord:
        return self.finish_tracking_run(run_id, status=TrackingRunStatus.PARTIAL, **kwargs)

    def mark_failed(self, run_id: int, **kwargs: Any) -> TrackingRunRecord:
        return self.finish_tracking_run(run_id, status=TrackingRunStatus.FAILED, **kwargs)


class SnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_profile_snapshot(
        self,
        profile_id: int,
        tracking_run_id: int,
        stats: ProfileStats,
        reviews: ReviewSummary | None = None,
        *,
        observed_at: datetime | None = None,
    ) -> ProfileSnapshotRecord | None:
        self._require_valid_run(tracking_run_id)
        values = self._profile_values(stats, reviews)
        latest = self.session.scalar(
            select(ProfileSnapshotRecord)
            .where(ProfileSnapshotRecord.profile_id == profile_id)
            .join(TrackingRunRecord)
            .where(TrackingRunRecord.status == TrackingRunStatus.VALID)
            .order_by(ProfileSnapshotRecord.observed_at.desc())
            .limit(1)
        )
        if latest is not None and self._same_values(latest, values):
            return None
        record = ProfileSnapshotRecord(
            profile_id=profile_id,
            tracking_run_id=tracking_run_id,
            observed_at=observed_at or _utc_now(),
            **values,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def save_listing_snapshot(
        self,
        listing_id: int,
        tracking_run_id: int,
        listing: Listing | None,
        *,
        observed_at: datetime | None = None,
        raw_json: dict[str, Any] | None = None,
        presence_state: PresenceState = PresenceState.ACTIVE,
    ) -> ListingSnapshotRecord | None:
        self._require_valid_run(tracking_run_id)
        if listing is None and presence_state != PresenceState.REMOVED:
            raise ValueError("An active snapshot requires listing data")
        latest = self.session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == listing_id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )
        if listing is None:
            if latest is None:
                raise WallapopError("Cannot create a removed snapshot without prior listing data")
            values = {key: getattr(latest, key) for key in self._LISTING_FIELDS}
        else:
            values = {
                "title": listing.title,
                "description": listing.description,
                "price": listing.price,
                "currency": listing.currency,
                "category_id": listing.category_id,
                "category_name": listing.category_name,
                "reserved": listing.reserved,
                "shipping_available": listing.shipping_available,
                "seller_allows_shipping": listing.seller_allows_shipping,
                "condition": listing.condition,
                "condition_code": listing.condition_code,
                "condition_label": listing.condition_label,
                "brand": listing.brand,
                "has_warranty": listing.has_warranty,
                "is_refurbished": listing.is_refurbished,
                "status": listing.status,
                "sale_status": listing.sale_status,
                "url": listing.url,
                "image_url": listing.image_url,
                "created_at_source": listing.created_at,
                "modified_at_source": listing.modified_at,
                "images_json": _json_text(listing.images_json),
                "attributes_json": _json_text(listing.attributes_json),
            }
        if (
            latest is not None
            and latest.presence_state == presence_state
            and self._same_values(latest, values)
        ):
            return None
        record = ListingSnapshotRecord(
            listing_id=listing_id,
            tracking_run_id=tracking_run_id,
            observed_at=observed_at or _utc_now(),
            presence_state=presence_state,
            raw_json=(
                json.dumps(raw_json, ensure_ascii=False, sort_keys=True)
                if raw_json is not None
                else None
            ),
            **values,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def mark_listing_seen(
        self, run_id: int, listing_id: int, *, observed_at: datetime | None = None
    ) -> TrackingRunListingRecord:
        run = self.session.get(TrackingRunRecord, run_id)
        if run is None:
            raise WallapopError(f"Tracking run not found: {run_id}")
        if run.status != TrackingRunStatus.VALID or not run.items_ok:
            raise WallapopError("Listing presence requires a valid, complete items run")
        existing = self.session.get(
            TrackingRunListingRecord, {"tracking_run_id": run_id, "listing_id": listing_id}
        )
        if existing is not None:
            return existing
        record = TrackingRunListingRecord(
            tracking_run_id=run_id,
            listing_id=listing_id,
            observed_at=observed_at or _utc_now(),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def was_listing_seen_in_run(self, run_id: int, listing_id: int) -> bool:
        return (
            self.session.get(
                TrackingRunListingRecord,
                {"tracking_run_id": run_id, "listing_id": listing_id},
            )
            is not None
        )

    @staticmethod
    def _profile_values(stats: ProfileStats, reviews: ReviewSummary | None) -> dict[str, Any]:
        distribution = reviews.rating_distribution if reviews else None
        rating = stats.rating if stats.rating is not None else (reviews.rating if reviews else None)
        return {
            "rating": Decimal(str(rating)) if rating is not None else None,
            "review_count": stats.review_count
            if stats.review_count is not None
            else (reviews.review_count if reviews else None),
            "published_count": stats.published_count,
            "purchases_count": stats.purchases_count,
            "sales_count": stats.sales_count,
            "sold_count": stats.sold_count,
            "reports_count": stats.reports_count,
            "rating_1_pct": distribution.get(1) if distribution else None,
            "rating_2_pct": distribution.get(2) if distribution else None,
            "rating_3_pct": distribution.get(3) if distribution else None,
            "rating_4_pct": distribution.get(4) if distribution else None,
            "rating_5_pct": distribution.get(5) if distribution else None,
        }

    @staticmethod
    def _same_values(record: object, values: dict[str, Any]) -> bool:
        return all(getattr(record, key) == value for key, value in values.items())

    _LISTING_FIELDS = (
        "title",
        "description",
        "price",
        "currency",
        "category_id",
        "category_name",
        "reserved",
        "shipping_available",
        "seller_allows_shipping",
        "condition",
        "condition_code",
        "condition_label",
        "brand",
        "has_warranty",
        "is_refurbished",
        "status",
        "sale_status",
        "url",
        "image_url",
        "created_at_source",
        "modified_at_source",
        "images_json",
        "attributes_json",
    )

    def _require_valid_run(self, run_id: int) -> TrackingRunRecord:
        run = self.session.get(TrackingRunRecord, run_id)
        if run is None:
            raise WallapopError(f"Tracking run not found: {run_id}")
        if run.status != TrackingRunStatus.VALID:
            raise WallapopError("Snapshots require a valid tracking run")
        return run
