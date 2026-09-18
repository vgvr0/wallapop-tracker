"""Tracking service for one explicitly monitored Wallapop listing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from wallapop_tracker.domain.alerts import AlertType, TrackingAlert
from wallapop_tracker.exceptions import WallapopNotFoundError, WallapopParseError
from wallapop_tracker.models import Listing
from wallapop_tracker.observability import get_metrics
from wallapop_tracker.providers.listing import ListingProvider
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    TrackedListingRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    SnapshotRepository,
    TrackedListingRepository,
    TrackingEventRepository,
    TrackingRunRepository,
)


@dataclass(frozen=True)
class ListingTrackingResult:
    tracked_listing_id: int
    run_id: int | None
    status: TrackingRunStatus | None
    alerts: tuple[TrackingAlert, ...] = ()
    error: str | None = None


class TrackedListingTracker:
    def __init__(
        self,
        provider: ListingProvider,
        session_factory: sessionmaker[Session] | Database,
    ) -> None:
        self.provider = provider
        self.session_factory = (
            session_factory.session_factory
            if isinstance(session_factory, Database)
            else session_factory
        )

    async def track_listing(self, tracked_listing_id: int) -> ListingTrackingResult:
        started_at = datetime.now(UTC)
        with self.session_factory() as session:
            tracked = TrackedListingRepository(session).get(tracked_listing_id)
            if tracked is None:
                raise ValueError(f"Unknown tracked listing: {tracked_listing_id}")
            if not tracked.enabled:
                return ListingTrackingResult(tracked_listing_id, None, None)
            item_id = tracked.listing.wallapop_item_id

        try:
            listing = await self.provider.get(item_id)
        except WallapopNotFoundError:
            return self._persist_removed(tracked_listing_id, started_at)
        except Exception as exc:
            if isinstance(exc, WallapopParseError):
                get_metrics().wallapop_parse_errors_total.labels("get_item").inc()
            return self._persist_failure(tracked_listing_id, started_at, exc)
        if listing.status is not None and listing.status.lower() in {
            "removed",
            "inactive",
            "deleted",
        }:
            return self._persist_removed(tracked_listing_id, started_at, listing)
        return self._persist_visible(tracked_listing_id, started_at, listing)

    def _persist_visible(
        self, tracked_listing_id: int, started_at: datetime, listing: Listing
    ) -> ListingTrackingResult:
        with self.session_factory.begin() as session:
            tracked = TrackedListingRepository(session).get(tracked_listing_id)
            if tracked is None:
                raise ValueError(f"Unknown tracked listing: {tracked_listing_id}")
            current = ListingRepository(session).get_listing_by_wallapop_id(listing.item_id)
            if current is None or current.id != tracked.listing_id:
                raise ValueError("Listing provider returned a different item")
            previous = self._latest_snapshot(session, current.id)
            runs = TrackingRunRepository(session)
            run = runs.start_listing_run(tracked_listing_id, started_at=started_at)
            runs.mark_valid(run.id, items_fetched=1, items_ok=True)
            ListingRepository(session).get_or_create_global_listing(
                listing, None, observed_at=started_at, tracking_run_id=run.id
            )
            SnapshotRepository(session).mark_listing_seen(
                run.id, current.id, observed_at=started_at
            )
            SnapshotRepository(session).save_listing_snapshot(
                current.id, run.id, listing, observed_at=started_at
            )
            alerts = self._events_for_transition(
                session, tracked, run.id, current, previous, listing, started_at
            )
            TrackedListingRepository(session).update_last_run(
                tracked_listing_id, started_at, TrackingRunStatus.VALID.value, run.id
            )
            return ListingTrackingResult(
                tracked_listing_id, run.id, TrackingRunStatus.VALID, tuple(alerts)
            )

    def _persist_removed(
        self,
        tracked_listing_id: int,
        started_at: datetime,
        listing: Listing | None = None,
    ) -> ListingTrackingResult:
        with self.session_factory.begin() as session:
            tracked = TrackedListingRepository(session).get(tracked_listing_id)
            if tracked is None:
                raise ValueError(f"Unknown tracked listing: {tracked_listing_id}")
            record = session.get(ListingRecord, tracked.listing_id)
            if record is None:
                return self._persist_failure_in_session(
                    session, tracked_listing_id, started_at, ValueError("Listing not found")
                )
            previous = self._latest_snapshot(session, record.id)
            if previous is None:
                return self._persist_failure_in_session(
                    session,
                    tracked_listing_id,
                    started_at,
                    ValueError("Cannot confirm removal without a prior snapshot"),
                )
            runs = TrackingRunRepository(session)
            run = runs.start_listing_run(tracked_listing_id, started_at=started_at)
            runs.mark_valid(run.id, items_fetched=0, items_ok=True)
            SnapshotRepository(session).save_listing_snapshot(
                record.id,
                run.id,
                listing,
                observed_at=started_at,
                presence_state=PresenceState.REMOVED,
            )
            alerts: list[TrackingAlert] = []
            if previous.presence_state != PresenceState.REMOVED:
                alerts.append(
                    self._create_event(
                        session,
                        tracked,
                        run.id,
                        record,
                        AlertType.REMOVED,
                        started_at,
                        None,
                        None,
                        {
                            "from": getattr(
                                previous.presence_state, "value", previous.presence_state
                            ),
                            "to": "removed",
                        },
                    )
                )
            TrackedListingRepository(session).update_last_run(
                tracked_listing_id, started_at, TrackingRunStatus.VALID.value, run.id
            )
            return ListingTrackingResult(
                tracked_listing_id, run.id, TrackingRunStatus.VALID, tuple(alerts)
            )

    def _persist_failure(
        self, tracked_listing_id: int, started_at: datetime, error: BaseException
    ) -> ListingTrackingResult:
        with self.session_factory.begin() as session:
            return self._persist_failure_in_session(
                session, tracked_listing_id, started_at, error
            )

    @staticmethod
    def _persist_failure_in_session(
        session: Session,
        tracked_listing_id: int,
        started_at: datetime,
        error: BaseException,
    ) -> ListingTrackingResult:
        tracked = TrackedListingRepository(session).get(tracked_listing_id)
        if tracked is None:
            raise ValueError(f"Unknown tracked listing: {tracked_listing_id}")
        run = TrackingRunRepository(session).start_listing_run(
            tracked_listing_id, started_at=started_at
        )
        TrackingRunRepository(session).mark_failed(
            run.id, error_type=type(error).__name__, error_message=str(error)
        )
        TrackedListingRepository(session).update_last_run(
            tracked_listing_id, started_at, TrackingRunStatus.FAILED.value, run.id
        )
        return ListingTrackingResult(
            tracked_listing_id, run.id, TrackingRunStatus.FAILED, error=str(error)
        )

    def _events_for_transition(
        self,
        session: Session,
        tracked: TrackedListingRecord,
        run_id: int,
        record: ListingRecord,
        previous: ListingSnapshotRecord | None,
        listing: Listing,
        created_at: datetime,
    ) -> list[TrackingAlert]:
        events: list[TrackingAlert] = []
        if previous is not None and previous.presence_state == PresenceState.REMOVED:
            events.append(
                self._create_event(
                    session,
                    tracked,
                    run_id,
                    record,
                    AlertType.REAPPEARED,
                    created_at,
                    None,
                    None,
                    {"from": "removed", "to": "active"},
                )
            )
            return events
        if previous is None or previous.presence_state == PresenceState.REMOVED:
            return events
        for field, event_type in (
            ("title", AlertType.TITLE_CHANGE),
            ("reserved", AlertType.RESERVATION_CHANGE),
            ("shipping_available", AlertType.SHIPPING_CHANGE),
            ("status", AlertType.STATUS_CHANGE),
        ):
            old = getattr(previous, field)
            new = getattr(listing, field)
            if old != new:
                events.append(
                    self._create_event(
                        session,
                        tracked,
                        run_id,
                        record,
                        event_type,
                        created_at,
                        None,
                        None,
                        {"field": field, "old": old, "new": new},
                    )
                )
        if (
            previous.price != listing.price
            and previous.price is not None
            and listing.price is not None
        ):
            event_type = (
                AlertType.PRICE_DROP
                if listing.price < previous.price
                else AlertType.PRICE_INCREASE
            )
            events.append(
                self._create_event(
                    session,
                    tracked,
                    run_id,
                    record,
                    event_type,
                    created_at,
                    previous.price,
                    listing.price,
                    {"old": str(previous.price), "new": str(listing.price)},
                )
            )
        return events

    @staticmethod
    def _create_event(
        session: Session,
        tracked: TrackedListingRecord,
        run_id: int,
        listing: ListingRecord,
        event_type: AlertType,
        created_at: datetime,
        old_price: Decimal | None,
        new_price: Decimal | None,
        transition: dict[str, object],
    ) -> TrackingAlert:
        key = json.dumps(
            {"event": event_type.value, "listing_id": listing.wallapop_item_id, **transition},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        event, _ = TrackingEventRepository(session).create_once(
            event_type=event_type.value,
            idempotency_key=key,
            listing_id=listing.id,
            tracking_run_id=run_id,
            tracked_search_id=None,
            old_price=old_price,
            new_price=new_price,
            created_at=created_at,
        )
        return TrackingAlert(
            event_id=event.id,
            type=event_type,
            created_at=created_at,
            listing_id=listing.wallapop_item_id,
            tracked_search_id=None,
            old_price=old_price,
            new_price=new_price,
            title=None,
            url=None,
            idempotency_key=key,
            tracked_listing_id=tracked.id,
        )

    @staticmethod
    def _latest_snapshot(
        session: Session, listing_id: int
    ) -> ListingSnapshotRecord | None:
        return session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == listing_id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )
