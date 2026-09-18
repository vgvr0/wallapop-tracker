"""Integrated, persistent tracking of public Wallapop searches."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from wallapop_tracker.domain.alerts import AlertType, TrackingAlert
from wallapop_tracker.domain.filters import filters_from_config
from wallapop_tracker.models import Listing
from wallapop_tracker.providers.search import SearchProvider, SearchRequest
from wallapop_tracker.services.relisting import RelistingDetectionService
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    TrackedSearchRecord,
    TrackingEventRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    SearchMatchRepository,
    SnapshotRepository,
    TrackedSearchRepository,
    TrackingEventRepository,
    TrackingRunRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchTrackingResult:
    search_id: int
    run_id: int | None
    status: TrackingRunStatus | None
    items_fetched: int
    matched_listings: int = 0
    new_listings: int = 0
    price_changes: int = 0
    duplicates_suppressed: int = 0
    alerts: tuple[TrackingAlert, ...] = ()
    error: str | None = None


class SearchTracker:
    """Capture one configured search and persist global listing history."""

    def __init__(
        self,
        provider: SearchProvider,
        session_factory: sessionmaker[Session] | Database,
    ) -> None:
        self.provider = provider
        self.session_factory = (
            session_factory.session_factory
            if isinstance(session_factory, Database)
            else session_factory
        )

    async def track_search(self, search_id: int) -> SearchTrackingResult:
        started_at = datetime.now(UTC)
        with self.session_factory() as session:
            search = TrackedSearchRepository(session).get(search_id)
            if search is None:
                raise ValueError(f"Unknown search: {search_id}")
            if not search.enabled:
                return SearchTrackingResult(search_id, None, None, 0)
            config = self._config(search)

        try:
            request = SearchRequest(
                query=search.query,
                min_price=search.min_price,
                max_price=search.max_price,
                category_id=self._optional_str(config.get("category_id")),
                condition=self._optional_str(config.get("condition")),
                brand=self._optional_str(config.get("brand")),
                shipping_required=(
                    bool(config["shipping_required"])
                    if "shipping_required" in config
                    else None
                ),
                latitude=self._optional_float(config.get("latitude")),
                longitude=self._optional_float(config.get("longitude")),
                distance=self._optional_float(config.get("distance")),
                max_pages=int(config.get("max_pages", 5)),
            )
            listings = await self.provider.search(request)
            filtered = filters_from_config(
                {
                    **config,
                    "min_price": str(search.min_price) if search.min_price is not None else None,
                    "max_price": str(search.max_price) if search.max_price is not None else None,
                }
            ).apply(listings)
            return self._persist_success(search_id, started_at, listings, filtered)
        except Exception as exc:
            return self._persist_failure(search_id, started_at, exc)

    def _persist_success(
        self,
        search_id: int,
        started_at: datetime,
        fetched: list[Listing],
        listings: list[Listing],
    ) -> SearchTrackingResult:
        with self.session_factory.begin() as session:
            search_repo = TrackedSearchRepository(session)
            search = search_repo.get(search_id)
            if search is None:
                raise ValueError(f"Unknown search: {search_id}")
            runs = TrackingRunRepository(session)
            initial_baseline = (
                not search_repo.has_valid_run(search_id) and not search.notify_on_first_run
            )
            run = runs.start_search_run(search_id, started_at=started_at)
            runs.mark_valid(run.id, items_fetched=len(fetched), items_ok=True)
            listing_repo = ListingRepository(session)
            snapshots = SnapshotRepository(session)
            matches = SearchMatchRepository(session)
            events = TrackingEventRepository(session)
            alerts: list[TrackingAlert] = []
            seen_ids: set[str] = set()
            new_count = price_count = duplicate_count = 0

            for listing in listings:
                if listing.item_id in seen_ids:
                    continue
                seen_ids.add(listing.item_id)
                existing = listing_repo.get_listing_by_wallapop_id(listing.item_id)
                previous_snapshot = self._latest_snapshot(session, existing)
                previous_match = (
                    matches.get(search_id, existing.id)
                    if existing is not None
                    else None
                )
                record, created = listing_repo.get_or_create_global_listing(
                    listing,
                    None,
                    observed_at=started_at,
                    tracking_run_id=run.id,
                )
                matches.touch(search_id, record.id, started_at)
                snapshots.mark_listing_seen(run.id, record.id, observed_at=started_at)
                snapshots.save_listing_snapshot(
                    record.id, run.id, listing, observed_at=started_at
                )

                if created:
                    try:
                        detection = RelistingDetectionService(session).detect_new_listing(
                            record,
                            listing,
                            tracking_run_id=run.id,
                            detected_at=started_at,
                        )
                    except Exception:
                        logger.exception(
                            "relisting_detection_failed listing_id=%s run_id=%s",
                            record.id,
                            run.id,
                        )
                    else:
                        if detection is not None:
                            relisting_event = session.get(
                                TrackingEventRecord, detection.event_id
                            )
                            if relisting_event is not None:
                                alerts.append(self._alert(relisting_event, listing, search_id))

                if previous_match is None and not initial_baseline:
                    new_key = self._event_key(AlertType.NEW_LISTING, listing.item_id)
                    new_event, created = events.create_once(
                        event_type=AlertType.NEW_LISTING.value,
                        idempotency_key=new_key,
                        listing_id=record.id,
                        tracking_run_id=run.id,
                        tracked_search_id=search_id,
                        old_price=None,
                        new_price=listing.price,
                        created_at=started_at,
                    )
                    if created:
                        new_count += 1
                        alerts.append(self._alert(new_event, listing, search_id))
                    else:
                        duplicate_count += 1

                if (
                    previous_snapshot is not None
                    and previous_snapshot.price is not None
                    and listing.price is not None
                    and previous_snapshot.price != listing.price
                ):
                    event_type = (
                        AlertType.PRICE_DROP
                        if listing.price < previous_snapshot.price
                        else AlertType.PRICE_INCREASE
                    )
                    key = self._event_key(
                        event_type,
                        listing.item_id,
                        previous_snapshot.price,
                        listing.price,
                    )
                    price_event, price_created = events.create_once(
                        event_type=event_type.value,
                        idempotency_key=key,
                        listing_id=record.id,
                        tracking_run_id=run.id,
                        tracked_search_id=search_id,
                        old_price=previous_snapshot.price,
                        new_price=listing.price,
                        created_at=started_at,
                    )
                    if price_created:
                        price_count += 1
                        alerts.append(self._alert(price_event, listing, search_id))
                    else:
                        duplicate_count += 1

            run.matched_listings = len(seen_ids)
            run.new_listings = new_count
            run.price_changes = price_count
            run.duplicates_suppressed = duplicate_count
            session.flush()
            search_repo.update_last_run(
                search_id, started_at, TrackingRunStatus.VALID.value, run.id
            )
            return SearchTrackingResult(
                search_id,
                run.id,
                TrackingRunStatus.VALID,
                len(fetched),
                len(seen_ids),
                new_count,
                price_count,
                duplicate_count,
                tuple(alerts),
            )

    def _persist_failure(
        self, search_id: int, started_at: datetime, error: BaseException
    ) -> SearchTrackingResult:
        with self.session_factory.begin() as session:
            search = TrackedSearchRepository(session).get(search_id)
            if search is None:
                raise ValueError(f"Unknown search: {search_id}")
            run = TrackingRunRepository(session).start_search_run(
                search_id, started_at=started_at
            )
            TrackingRunRepository(session).mark_failed(
                run.id, error_type=type(error).__name__, error_message=str(error)
            )
            TrackedSearchRepository(session).update_last_run(
                search_id, started_at, TrackingRunStatus.FAILED.value, run.id
            )
            return SearchTrackingResult(
                search_id, run.id, TrackingRunStatus.FAILED, 0, error=str(error)
            )

    @staticmethod
    def _latest_snapshot(
        session: Session, listing: ListingRecord | None
    ) -> ListingSnapshotRecord | None:
        if listing is None:
            return None
        return session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == listing.id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )

    @staticmethod
    def _config(search: TrackedSearchRecord) -> dict[str, Any]:
        if not search.filters_json:
            return {}
        value = json.loads(search.filters_json)
        if not isinstance(value, dict):
            raise ValueError("tracked search filters must be a JSON object")
        return value

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, (float, int, str)):
            return float(value)
        raise ValueError("search location values must be numeric")

    @staticmethod
    def _event_key(
        event_type: AlertType,
        item_id: str,
        old_price: Decimal | None = None,
        new_price: Decimal | None = None,
    ) -> str:
        return json.dumps(
            {
                "event": event_type.value,
                "listing_id": item_id,
                "old_price": str(old_price) if old_price is not None else None,
                "new_price": str(new_price) if new_price is not None else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _alert(
        event: TrackingEventRecord, listing: Listing, search_id: int
    ) -> TrackingAlert:
        return TrackingAlert(
            event.id,
            AlertType(event.event_type),
            event.created_at,
            listing.item_id,
            search_id,
            event.old_price,
            event.new_price,
            listing.title,
            listing.url,
            event.idempotency_key,
        )
