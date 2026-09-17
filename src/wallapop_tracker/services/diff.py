"""Historical diffing of valid tracking runs.

Presence comes exclusively from ``tracking_run_listings``.  Listing and
profile snapshots are change-based, so their effective value is the latest
snapshot attached to a valid run whose run order is at or before the target
run.  A missing snapshot therefore means "no known state", rather than a
value reset.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.domain.changes import ChangeType, DetectedChange
from wallapop_tracker.storage.models import (
    ListingSnapshotRecord,
    ProfileSnapshotRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


@dataclass(frozen=True, order=True)
class _RunKey:
    started_at: datetime
    run_id: int


SnapshotT = TypeVar("SnapshotT", ListingSnapshotRecord, ProfileSnapshotRecord)


class DiffService:
    """Compare two runs without writing to the database.

    ``previous_run_id`` may be ``None`` to explicitly request the baseline
    behaviour.  The first valid capture produces no mass ``NEW_LISTING``
    changes; callers can compare it with a later run to detect additions.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def compare_runs(
        self, previous_run_id: int | None, current_run_id: int
    ) -> list[DetectedChange]:
        current = self._valid_run(current_run_id)
        if previous_run_id is None:
            return []
        previous = self._valid_run(previous_run_id)
        if previous.profile_id != current.profile_id:
            raise ValueError("Runs must belong to the same profile")
        if self._run_key(previous) >= self._run_key(current):
            raise ValueError("Current run must be later than previous run")

        valid_runs = self._valid_runs(current.profile_id)
        prior_runs = [run for run in valid_runs if self._run_key(run) < self._run_key(current)]
        presence = self._presence(valid_runs)
        previous_ids = presence.get(previous.id, set())
        current_ids = presence.get(current.id, set())
        all_prior_ids = set().union(*(presence.get(run.id, set()) for run in prior_runs))

        changes: list[DetectedChange] = []
        for listing_id in sorted(current_ids - all_prior_ids):
            changes.append(self._change(ChangeType.NEW_LISTING, current, None, listing_id))
        for listing_id in sorted(current_ids - previous_ids):
            prior_indexes = [
                index
                for index, run in enumerate(prior_runs)
                if listing_id in presence.get(run.id, set())
            ]
            if prior_indexes and prior_indexes[-1] < len(prior_runs) - 1:
                changes.append(self._change(ChangeType.REAPPEARED, current, previous, listing_id))
        for listing_id in sorted(previous_ids - current_ids):
            changes.append(self._change(ChangeType.REMOVED, current, previous, listing_id))

        listing_ids = sorted(previous_ids & current_ids)
        listing_states = self._effective_listing_states(listing_ids, valid_runs, previous, current)
        for listing_id in listing_ids:
            old, new = listing_states.get(listing_id, (None, None))
            changes.extend(self._listing_field_changes(current, previous, listing_id, old, new))

        changes.extend(self._profile_metric_changes(current, previous, valid_runs))
        return sorted(changes, key=self._sort_key)

    def _valid_run(self, run_id: int) -> TrackingRunRecord:
        run = self.session.get(TrackingRunRecord, run_id)
        if run is None:
            raise ValueError(f"Tracking run not found: {run_id}")
        if run.status != TrackingRunStatus.VALID:
            raise ValueError(f"Tracking run {run_id} is not valid")
        return run

    def _valid_runs(self, profile_id: int) -> list[TrackingRunRecord]:
        return list(
            self.session.scalars(
                select(TrackingRunRecord)
                .where(
                    TrackingRunRecord.profile_id == profile_id,
                    TrackingRunRecord.status == TrackingRunStatus.VALID,
                )
                .order_by(TrackingRunRecord.started_at, TrackingRunRecord.id)
            )
        )

    @staticmethod
    def _run_key(run: TrackingRunRecord) -> _RunKey:
        return _RunKey(run.started_at, run.id)

    def _presence(self, runs: Iterable[TrackingRunRecord]) -> dict[int, set[int]]:
        run_ids = [run.id for run in runs]
        if not run_ids:
            return {}
        rows = self.session.execute(
            select(
                TrackingRunListingRecord.tracking_run_id, TrackingRunListingRecord.listing_id
            ).where(TrackingRunListingRecord.tracking_run_id.in_(run_ids))
        )
        result: dict[int, set[int]] = {run_id: set() for run_id in run_ids}
        for run_id, listing_id in rows:
            result[run_id].add(listing_id)
        return result

    def _effective_listing_states(
        self,
        listing_ids: list[int],
        valid_runs: list[TrackingRunRecord],
        previous: TrackingRunRecord,
        current: TrackingRunRecord,
    ) -> dict[int, tuple[ListingSnapshotRecord | None, ListingSnapshotRecord | None]]:
        if not listing_ids:
            return {}
        snapshots = list(
            self.session.scalars(
                select(ListingSnapshotRecord)
                .join(
                    TrackingRunRecord, ListingSnapshotRecord.tracking_run_id == TrackingRunRecord.id
                )
                .where(
                    ListingSnapshotRecord.listing_id.in_(listing_ids),
                    TrackingRunRecord.status == TrackingRunStatus.VALID,
                )
                .order_by(ListingSnapshotRecord.observed_at, ListingSnapshotRecord.id)
            )
        )
        run_by_id = {run.id: run for run in valid_runs}
        result: dict[int, tuple[ListingSnapshotRecord | None, ListingSnapshotRecord | None]] = {}
        for listing_id in listing_ids:
            relevant = [snapshot for snapshot in snapshots if snapshot.listing_id == listing_id]
            old = self._latest_before(relevant, previous, run_by_id)
            new = self._latest_before(relevant, current, run_by_id)
            result[listing_id] = (old, new)
        return result

    @staticmethod
    def _latest_before(
        snapshots: list[SnapshotT],
        target: TrackingRunRecord,
        run_by_id: dict[int, TrackingRunRecord],
    ) -> SnapshotT | None:
        return max(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.tracking_run_id in run_by_id
                and DiffService._run_key(run_by_id[snapshot.tracking_run_id])
                <= DiffService._run_key(target)
            ),
            key=lambda snapshot: (snapshot.observed_at, snapshot.id),
            default=None,
        )

    def _profile_metric_changes(
        self,
        current: TrackingRunRecord,
        previous: TrackingRunRecord,
        valid_runs: list[TrackingRunRecord],
    ) -> list[DetectedChange]:
        snapshots = list(
            self.session.scalars(
                select(ProfileSnapshotRecord)
                .where(ProfileSnapshotRecord.profile_id == current.profile_id)
                .join(
                    TrackingRunRecord, ProfileSnapshotRecord.tracking_run_id == TrackingRunRecord.id
                )
                .where(TrackingRunRecord.status == TrackingRunStatus.VALID)
                .order_by(ProfileSnapshotRecord.observed_at, ProfileSnapshotRecord.id)
            )
        )
        run_by_id = {run.id: run for run in valid_runs}
        old = self._latest_before(snapshots, previous, run_by_id)
        new = self._latest_before(snapshots, current, run_by_id)
        if old is None or new is None:
            return []
        mapping = (
            ("review_count", ChangeType.REVIEW_COUNT_CHANGED),
            ("rating", ChangeType.RATING_CHANGED),
            ("sold_count", ChangeType.SOLD_COUNT_CHANGED),
        )
        return [
            self._change(kind, current, previous, None, getattr(old, field), getattr(new, field))
            for field, kind in mapping
            if getattr(old, field) != getattr(new, field)
        ]

    def _listing_field_changes(
        self,
        current: TrackingRunRecord,
        previous: TrackingRunRecord,
        listing_id: int,
        old: ListingSnapshotRecord | None,
        new: ListingSnapshotRecord | None,
    ) -> list[DetectedChange]:
        if old is None or new is None:
            return []
        result: list[DetectedChange] = []
        if old.price != new.price:
            result.append(
                self._change(
                    ChangeType.PRICE_CHANGED, current, previous, listing_id, old.price, new.price
                )
            )
        if old.title != new.title:
            result.append(
                self._change(
                    ChangeType.TITLE_CHANGED, current, previous, listing_id, old.title, new.title
                )
            )
        if old.reserved != new.reserved:
            kind = ChangeType.RESERVED if new.reserved else ChangeType.UNRESERVED
            result.append(
                self._change(kind, current, previous, listing_id, old.reserved, new.reserved)
            )
        if old.shipping_available != new.shipping_available:
            result.append(
                self._change(
                    ChangeType.SHIPPING_AVAILABLE_CHANGED,
                    current,
                    previous,
                    listing_id,
                    old.shipping_available,
                    new.shipping_available,
                )
            )
        if old.brand != new.brand:
            result.append(
                self._change(
                    ChangeType.BRAND_CHANGED,
                    current,
                    previous,
                    listing_id,
                    old.brand,
                    new.brand,
                )
            )
        return result

    @staticmethod
    def _change(
        kind: ChangeType,
        current: TrackingRunRecord,
        previous: TrackingRunRecord | None,
        listing_id: int | None,
        old: object | None = None,
        new: object | None = None,
    ) -> DetectedChange:
        return DetectedChange(
            kind,
            current.profile_id,
            listing_id,
            previous.id if previous else None,
            current.id,
            old,
            new,
        )

    @staticmethod
    def _sort_key(change: DetectedChange) -> tuple[int, int]:
        order = {
            ChangeType.REVIEW_COUNT_CHANGED: 0,
            ChangeType.RATING_CHANGED: 1,
            ChangeType.SOLD_COUNT_CHANGED: 2,
            ChangeType.NEW_LISTING: 3,
            ChangeType.REAPPEARED: 4,
            ChangeType.PRICE_CHANGED: 5,
            ChangeType.TITLE_CHANGED: 6,
            ChangeType.RESERVED: 7,
            ChangeType.UNRESERVED: 7,
            ChangeType.SHIPPING_AVAILABLE_CHANGED: 8,
            ChangeType.BRAND_CHANGED: 9,
            ChangeType.REMOVED: 10,
        }
        return order[change.change_type], change.listing_id or -1


DiffEngine = DiffService
