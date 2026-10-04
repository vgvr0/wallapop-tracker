"""Derived read-only metrics over historical query results."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from wallapop_tracker.reporting.queries import (
    _latest_listing_snapshots,
    _presence_by_run,
    _valid_runs,
)
from wallapop_tracker.storage.models import (
    ListingSaleStatus,
    ListingSnapshotRecord,
    PresenceState,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)

from .queries import (
    get_inventory_history,
    get_profile_metrics_history,
)


@dataclass(frozen=True)
class WeeklyProfileSummary:
    run_id: int
    observed_at: datetime
    inventory_count: int
    new_listings: int
    removed_listings: int
    price_decreases: int
    price_increases: int
    review_count: int | None
    review_count_delta: int | None
    sold_count: int | None
    sold_count_delta: int | None
    average_active_price: Decimal | None
    priced_listing_count: int


@dataclass(frozen=True)
class ProfileInventoryStats:
    """Current asking-price statistics for one tracked profile."""

    profile_alias: str
    active_count: int
    reserved_count: int
    total_count: int
    priced_count: int
    currency: str | None
    total_value: Decimal
    average_price: Decimal | None
    median_price: Decimal | None
    minimum_price: Decimal | None
    maximum_price: Decimal | None


def _latest_complete_profile_run(session: Session, profile_id: int) -> TrackingRunRecord | None:
    return session.scalar(
        select(TrackingRunRecord)
        .where(
            TrackingRunRecord.profile_id == profile_id,
            TrackingRunRecord.status == TrackingRunStatus.VALID,
            TrackingRunRecord.items_ok.is_(True),
        )
        .order_by(TrackingRunRecord.started_at.desc(), TrackingRunRecord.id.desc())
        .limit(1)
    )


def _latest_complete_snapshots(
    session: Session, listing_ids: set[int], run: TrackingRunRecord
) -> dict[int, ListingSnapshotRecord]:
    if not listing_ids:
        return {}
    rows = session.scalars(
        select(ListingSnapshotRecord)
        .join(TrackingRunRecord, TrackingRunRecord.id == ListingSnapshotRecord.tracking_run_id)
        .where(
            ListingSnapshotRecord.listing_id.in_(listing_ids),
            TrackingRunRecord.status == TrackingRunStatus.VALID,
            TrackingRunRecord.items_ok.is_(True),
            or_(
                TrackingRunRecord.started_at < run.started_at,
                and_(
                    TrackingRunRecord.started_at == run.started_at,
                    TrackingRunRecord.id <= run.id,
                ),
            ),
        )
        .order_by(
            TrackingRunRecord.started_at,
            TrackingRunRecord.id,
            ListingSnapshotRecord.id,
        )
    ).all()
    latest: dict[int, ListingSnapshotRecord] = {}
    for row in rows:
        latest[row.listing_id] = row
    return latest


def get_profile_inventory_stats(
    session: Session, profile_id: int, profile_alias: str
) -> ProfileInventoryStats:
    """Calculate statistics from the latest complete profile tracking run.

    Run membership identifies the current inventory.  Snapshot state and price
    are read only for those members, so historical snapshots cannot inflate the
    result and removed listings cannot leak back into it.
    """
    run = _latest_complete_profile_run(session, profile_id)
    if run is None:
        return ProfileInventoryStats(
            profile_alias,
            active_count=0,
            reserved_count=0,
            total_count=0,
            priced_count=0,
            currency=None,
            total_value=Decimal("0"),
            average_price=None,
            median_price=None,
            minimum_price=None,
            maximum_price=None,
        )

    present_ids = set(
        session.scalars(
            select(TrackingRunListingRecord.listing_id).where(
                TrackingRunListingRecord.tracking_run_id == run.id
            )
        )
    )
    snapshots = _latest_complete_snapshots(session, present_ids, run)
    current = [
        snapshot
        for snapshot in snapshots.values()
        if snapshot.presence_state == PresenceState.ACTIVE
        and snapshot.sale_status in {ListingSaleStatus.ACTIVE, ListingSaleStatus.RESERVED}
    ]
    active_count = sum(snapshot.sale_status == ListingSaleStatus.ACTIVE for snapshot in current)
    reserved_count = sum(snapshot.sale_status == ListingSaleStatus.RESERVED for snapshot in current)
    prices = [snapshot.price for snapshot in current if snapshot.price is not None]
    currencies = {
        snapshot.currency.strip().upper()
        for snapshot in current
        if snapshot.price is not None
        and snapshot.currency is not None
        and snapshot.currency.strip()
    }
    has_unknown_currency = any(
        snapshot.price is not None and not snapshot.currency for snapshot in current
    )
    if len(currencies) > 1 or (currencies and has_unknown_currency):
        labels = ", ".join(sorted(currencies)) or "unknown"
        if has_unknown_currency:
            labels = f"{labels}, unknown"
        raise ValueError(
            f"Cannot aggregate inventory prices for mixed or unknown currencies: {labels}"
        )

    currency = next(iter(currencies), None)
    total_value = sum(prices, Decimal("0"))
    return ProfileInventoryStats(
        profile_alias,
        active_count=active_count,
        reserved_count=reserved_count,
        total_count=active_count + reserved_count,
        priced_count=len(prices),
        currency=currency,
        total_value=total_value,
        average_price=total_value / len(prices) if prices else None,
        median_price=median(prices) if prices else None,
        minimum_price=min(prices) if prices else None,
        maximum_price=max(prices) if prices else None,
    )


def get_average_active_price(
    session: Session, profile_id: int, run_id: int
) -> tuple[Decimal | None, int]:
    runs = _valid_runs(session, profile_id)
    run = next((item for item in runs if item.id == run_id), None)
    if run is None:
        return None, 0
    presence = _presence_by_run(session, [run_id]).get(run_id, set())
    values = [
        price
        for _, price in _latest_listing_snapshots(session, list(presence), run.started_at).values()
        if price is not None
    ]
    if not values:
        return None, 0
    return sum(values, Decimal("0")) / len(values), len(values)


def get_weekly_summary(session: Session, profile_id: int) -> list[WeeklyProfileSummary]:
    runs = _valid_runs(session, profile_id)
    inventory = {point.run_id: point.count for point in get_inventory_history(session, profile_id)}
    metrics = get_profile_metrics_history(session, profile_id)
    presence = _presence_by_run(session, [run.id for run in runs])
    summaries: list[WeeklyProfileSummary] = []
    previous_metrics = None
    previous_run = None
    for run, metric in zip(runs, metrics, strict=True):
        current_ids = presence.get(run.id, set())
        previous_ids = presence.get(previous_run.id, set()) if previous_run else set()
        prices = _latest_listing_snapshots(session, list(current_ids), run.started_at)
        price_decreases = price_increases = 0
        if previous_run:
            old = _latest_listing_snapshots(session, list(previous_ids), previous_run.started_at)
            for listing_id in current_ids & previous_ids:
                before = old.get(listing_id, (None, None))[1]
                after = prices.get(listing_id, (None, None))[1]
                if before is not None and after is not None:
                    price_decreases += after < before
                    price_increases += after > before
        average, priced_count = get_average_active_price(session, profile_id, run.id)
        new_count = len(current_ids - previous_ids) if previous_run else 0
        removed_count = len(previous_ids - current_ids) if previous_run else 0
        summaries.append(
            WeeklyProfileSummary(
                run.id,
                run.started_at,
                inventory[run.id],
                new_count,
                removed_count,
                price_decreases,
                price_increases,
                metric.review_count,
                (
                    metric.review_count - previous_metrics.review_count
                    if previous_metrics
                    and metric.review_count is not None
                    and previous_metrics.review_count is not None
                    else None
                ),
                metric.sold_count,
                (
                    metric.sold_count - previous_metrics.sold_count
                    if previous_metrics
                    and metric.sold_count is not None
                    and previous_metrics.sold_count is not None
                    else None
                ),
                average,
                priced_count,
            )
        )
        previous_run, previous_metrics = run, metric
    return summaries
