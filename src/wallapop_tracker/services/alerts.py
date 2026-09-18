from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.domain.alerts import AlertType, PriceDropAlert, SearchMatchAlert
from wallapop_tracker.models import Listing
from wallapop_tracker.storage.models import (
    ListingSnapshotRecord,
    PriceWatchRecord,
    SavedSearchItemRecord,
    SavedSearchRecord,
)

SearchRunner = Callable[[SavedSearchRecord], Awaitable[Sequence[Listing]]]


class SearchAlertService:
    """Legacy saved-search alerts retained for database compatibility.

    .. deprecated::
       New application code must use ``TrackedSearch`` and the persistent
       ``TrackingEventRecord`` ledger through ``SearchTracker``. This service
       is intentionally not used by the CLI or scheduler and will be removed
       after legacy data has a documented migration path.
    """

    def __init__(self, session: Session, runner: SearchRunner) -> None:
        self.session, self.runner = session, runner

    async def check_search(self, saved_search_id: int) -> list[SearchMatchAlert]:
        search = self.session.get(SavedSearchRecord, saved_search_id)
        if search is None or not search.enabled:
            return []
        now = datetime.now(UTC)
        results = await self.runner(search)
        known = set(
            self.session.scalars(
                select(SavedSearchItemRecord.wallapop_item_id).where(
                    SavedSearchItemRecord.saved_search_id == saved_search_id
                )
            )
        )
        alerts: list[SearchMatchAlert] = []
        for item in results:
            row = self.session.get(SavedSearchItemRecord, (saved_search_id, item.item_id))
            if row is None:
                self.session.add(
                    SavedSearchItemRecord(
                        saved_search_id=saved_search_id,
                        wallapop_item_id=item.item_id,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
                if known:
                    alerts.append(
                        SearchMatchAlert(
                            AlertType.NEW_SEARCH_MATCH,
                            now,
                            item.item_id,
                            saved_search_id,
                            None,
                            item.price,
                            item.title,
                            item.url,
                        )
                    )
            else:
                row.last_seen_at = now
        search.last_checked_at = now
        self.session.commit()
        return alerts

    async def check_all_searches(self) -> list[SearchMatchAlert]:
        result: list[SearchMatchAlert] = []
        for search in self.session.scalars(
            select(SavedSearchRecord).order_by(SavedSearchRecord.id)
        ):
            result.extend(await self.check_search(search.id))
        return result


class PriceAlertService:
    """Legacy price-watch reader retained until ``TrackedListing`` exists.

    .. deprecated::
       New search alerts are emitted by ``SearchTracker``. Direct listing
       watches remain available temporarily because they are a distinct use
       case and will migrate to ``TrackedListing`` in a later phase.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def check_price_watch(self, watch_id: int) -> PriceDropAlert | None:
        watch = self.session.get(PriceWatchRecord, watch_id)
        if watch is None or not watch.enabled:
            return None
        snapshots = list(
            self.session.scalars(
                select(ListingSnapshotRecord)
                .where(ListingSnapshotRecord.listing_id == watch.listing_id)
                .order_by(ListingSnapshotRecord.observed_at, ListingSnapshotRecord.id)
            )
        )
        if len(snapshots) < 2:
            return None
        old, new = snapshots[-2].price, snapshots[-1].price
        if old is None or new is None or new >= old:
            return None
        if watch.last_notified_price == new:
            return None
        watch.last_notified_price = new
        self.session.commit()
        return PriceDropAlert(
            AlertType.PRICE_DROP,
            snapshots[-1].observed_at,
            watch.listing_id,
            None,
            old,
            new,
            snapshots[-1].title,
            snapshots[-1].url,
        )

    def check_all_price_watches(self) -> list[PriceDropAlert]:
        return [
            alert
            for watch in self.session.scalars(select(PriceWatchRecord))
            if (alert := self.check_price_watch(watch.id)) is not None
        ]
