"""Derived read-only metrics over historical query results."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from wallapop_tracker.reporting.queries import (
    _latest_listing_snapshots,
    _presence_by_run,
    _valid_runs,
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
        for _, price in _latest_listing_snapshots(
            session, list(presence), run.started_at
        ).values()
        if price is not None
    ]
    if not values:
        return None, 0
    return sum(values, Decimal("0")) / len(values), len(values)


def get_weekly_summary(session: Session, profile_id: int) -> list[WeeklyProfileSummary]:
    runs = _valid_runs(session, profile_id)
    inventory = {
        point.run_id: point.count
        for point in get_inventory_history(session, profile_id)
    }
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
        summaries.append(WeeklyProfileSummary(
            run.id, run.started_at, inventory[run.id],
            new_count, removed_count,
            price_decreases, price_increases, metric.review_count,
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
            average, priced_count,
        ))
        previous_run, previous_metrics = run, metric
    return summaries
