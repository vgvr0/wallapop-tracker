"""Repositories for isolated historical persistence operations."""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.exceptions import WallapopError
from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary

from .models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    ProfileRecord,
    ProfileSnapshotRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            self.session.add(record)
            self.session.flush()
            return record
        if record.profile_id != profile_id:
            raise WallapopError(
                f"Listing {listing.item_id} changed profile from "
                f"{record.profile_id} to {profile_id}"
            )
        if valid_observation:
            record.last_seen_at = now
            record.updated_at = now
        self.session.flush()
        return record


class TrackingRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start_tracking_run(
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
