"""Repositories for isolated historical persistence operations."""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wallapop_tracker.exceptions import WallapopError
from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary

from .models import (
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
    ) -> TrackedSearchRecord:
        query = query.strip()
        if not query:
            raise ValueError("query is required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise ValueError("min_price must not exceed max_price")
        now = datetime.now(UTC)
        record = TrackedSearchRecord(
            name=name.strip() if name and name.strip() else None,
            query=query,
            min_price=min_price,
            max_price=max_price,
            filters_json=_json_text(filters),
            interval_seconds=interval_seconds,
            notify_on_first_run=notify_on_first_run,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def has_valid_run(self, search_id: int) -> bool:
        return self.session.scalar(
            select(TrackingRunRecord.id)
            .where(
                TrackingRunRecord.tracked_search_id == search_id,
                TrackingRunRecord.status == TrackingRunStatus.VALID,
            )
            .limit(1)
        ) is not None

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
    ) -> TrackedListingRecord:
        alias = alias.strip()
        if not alias:
            raise ValueError("alias is required")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self.session.get(ListingRecord, listing_id) is None:
            raise ValueError(f"Unknown listing: {listing_id}")
        now = _utc_now()
        record = TrackedListingRecord(
            listing_id=listing_id,
            alias=alias,
            interval_seconds=interval_seconds,
            notes=notes,
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

    def remove(self, value: str) -> None:
        record = self.get_by_alias_or_id(value)
        if record is None:
            raise ValueError(f"Unknown tracked listing: {value}")
        if self.session.scalar(
            select(TrackingRunRecord.id)
            .where(TrackingRunRecord.tracked_listing_id == record.id)
            .limit(1)
        ) is not None:
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
                )
                self.session.add(record)
                self.session.flush()
        except IntegrityError:
            existing = self.get_by_key(idempotency_key)
            if existing is None:
                raise
            return existing, False
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
        return self.session.scalar(
            select(ListingRecord).where(ListingRecord.wallapop_item_id == item_id)
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
        record = self.get_listing_by_wallapop_id(listing.item_id)
        if record is None:
            if tracking_run_id is not None and not valid_observation:
                raise WallapopError("A non-valid tracking run cannot establish listing presence")
            record = ListingRecord(
                wallapop_item_id=listing.item_id,
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
        record = self.get_listing_by_wallapop_id(listing.item_id)
        created = record is None
        if record is None:
            if tracking_run_id is not None and not valid_observation:
                raise WallapopError("A non-valid tracking run cannot establish listing presence")
            record = ListingRecord(
                wallapop_item_id=listing.item_id,
                profile_id=profile_id,
                seller_user_id=listing.user_id or None,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(record)
                    self.session.flush()
            except IntegrityError:
                existing = self.get_listing_by_wallapop_id(listing.item_id)
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
        self.session.flush()
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
                "brand": listing.brand,
                "has_warranty": listing.has_warranty,
                "is_refurbished": listing.is_refurbished,
                "status": listing.status,
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
        "brand",
        "has_warranty",
        "is_refurbished",
        "status",
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
