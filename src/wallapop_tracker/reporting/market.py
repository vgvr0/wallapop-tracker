"""Read-only, reproducible market analytics for tracked searches."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PossibleRelistingRecord,
    SearchListingMatchRecord,
    TrackingEventRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


@dataclass(frozen=True)
class MarketSummary:
    search_id: int
    observed_from: datetime | None
    observed_to: datetime | None
    active_listings: int
    unique_listings: int
    median_price: Decimal | None
    average_price: Decimal | None
    p25_price: Decimal | None
    p75_price: Decimal | None
    new_listings: int
    removed_listings: int
    price_drops: int
    price_increases: int
    median_active_duration: timedelta | None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    priced_listing_count: int = 0
    possible_relisting_count: int = 0


@dataclass(frozen=True)
class PriceBin:
    lower: Decimal
    upper: Decimal
    count: int


@dataclass(frozen=True)
class MarketPricePoint:
    date: datetime
    median_price: Decimal | None
    average_price: Decimal | None
    active_listings: int


@dataclass(frozen=True)
class MarketActivityPoint:
    date: datetime
    new_listings: int
    removed_listings: int
    price_drops: int


@dataclass(frozen=True)
class SellerMarketStats:
    profile_id: int | None
    seller_external_id: str | None
    listing_count: int
    active_count: int
    median_price: Decimal | None
    price_drop_count: int


@dataclass(frozen=True)
class BrandMarketStats:
    brand: str
    listing_count: int
    active_count: int
    median_price: Decimal | None


@dataclass(frozen=True)
class CategoryMarketStats:
    category_id: str
    category_name: str | None
    listing_count: int
    active_count: int
    median_price: Decimal | None


def get_market_summary(
    session: Session,
    search_id: int,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    exclude_listing_id: int | None = None,
) -> MarketSummary:
    runs = _search_runs(session, search_id, end_at=end_at)
    period_runs = [run for run in runs if _in_period(run.started_at, start_at, end_at)]
    end = end_at or (runs[-1].started_at if runs else None)
    match_rows = _matches(session, search_id, end)
    listing_ids = {row.listing_id for row in match_rows}
    active_ids = _active_ids(session, runs, end)
    if exclude_listing_id is not None:
        active_ids.discard(exclude_listing_id)
    prices = _prices_at(session, active_ids, end)
    stats = _price_stats(prices.values())
    removed = _removed_transitions(session, runs, start_at, end_at)
    new_count = sum(1 for row in match_rows if _in_period(row.first_seen_at, start_at, end_at))
    drop_count, increase_count = _price_event_counts(session, search_id, start_at, end_at)
    durations = _observed_durations(
        session,
        match_rows,
        _removed_transitions(session, runs, None, end_at),
        end,
    )
    possible = 0
    if listing_ids:
        possible_statement = (
            select(func.count())
            .select_from(PossibleRelistingRecord)
            .where(PossibleRelistingRecord.current_listing_id.in_(listing_ids))
        )
        if start_at is not None:
            possible_statement = possible_statement.where(
                PossibleRelistingRecord.detected_at >= start_at
            )
        if end_at is not None:
            possible_statement = possible_statement.where(
                PossibleRelistingRecord.detected_at <= end_at
            )
        possible = session.scalar(possible_statement) or 0
    return MarketSummary(
        search_id=search_id,
        observed_from=_utc(period_runs[0].started_at) if period_runs else None,
        observed_to=_utc(period_runs[-1].started_at) if period_runs else None,
        active_listings=len(active_ids),
        unique_listings=len(listing_ids),
        median_price=stats[0],
        average_price=stats[1],
        p25_price=stats[2],
        p75_price=stats[3],
        new_listings=new_count,
        removed_listings=len(removed),
        price_drops=drop_count,
        price_increases=increase_count,
        median_active_duration=_decimal_timedelta(durations),
        min_price=stats[4],
        max_price=stats[5],
        priced_listing_count=sum(price is not None for price in prices.values()),
        possible_relisting_count=int(possible),
    )


def get_price_distribution(
    session: Session,
    search_id: int,
    *,
    bins: int = 10,
    end_at: datetime | None = None,
) -> list[PriceBin]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    runs = _search_runs(session, search_id, end_at=end_at)
    raw_prices = _prices_at(session, _active_ids(session, runs, end_at), end_at).values()
    prices = [price for price in raw_prices if price is not None]
    if not prices:
        return []
    lower, upper = min(prices), max(prices)
    if lower == upper:
        return [PriceBin(lower, upper, len(prices))]
    width = (upper - lower) / Decimal(bins)
    counts = [0] * bins
    for price in prices:
        index = int((price - lower) / width)
        counts[min(index, bins - 1)] += 1
    return [
        PriceBin(lower + width * index, lower + width * (index + 1), counts[index])
        for index in range(bins)
    ]


def get_price_time_series(
    session: Session,
    search_id: int,
    *,
    granularity: str = "daily",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> list[MarketPricePoint]:
    runs = _period_runs(session, search_id, start_at, end_at)
    selected = _last_run_per_bucket(runs, granularity)
    points = []
    for run in selected:
        active = _active_ids(
            session, _search_runs(session, search_id, end_at=run.started_at), run.started_at
        )
        stats = _price_stats(_prices_at(session, active, run.started_at).values())
        points.append(
            MarketPricePoint(_bucket(run.started_at, granularity), stats[0], stats[1], len(active))
        )
    return points


def get_activity_time_series(
    session: Session,
    search_id: int,
    *,
    granularity: str = "daily",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> list[MarketActivityPoint]:
    if granularity not in {"daily", "weekly"}:
        raise ValueError("granularity must be daily or weekly")
    runs = _search_runs(session, search_id, end_at=end_at)
    new_by_bucket: defaultdict[datetime, int] = defaultdict(int)
    for match in _matches(session, search_id, end_at):
        if _in_period(match.first_seen_at, start_at, end_at):
            new_by_bucket[_bucket(match.first_seen_at, granularity)] += 1
    removed = _removed_transitions(session, runs, start_at, end_at)
    removed_by_bucket: defaultdict[datetime, int] = defaultdict(int)
    for timestamp, _ in removed.values():
        removed_by_bucket[_bucket(timestamp, granularity)] += 1
    drops = _events_by_bucket(session, search_id, "PRICE_DROP", start_at, end_at, granularity)
    buckets = sorted(set(new_by_bucket) | set(removed_by_bucket) | set(drops))
    return [
        MarketActivityPoint(
            bucket,
            new_by_bucket[bucket],
            removed_by_bucket[bucket],
            drops[bucket],
        )
        for bucket in buckets
    ]


def get_seller_market_stats(
    session: Session,
    search_id: int,
    *,
    end_at: datetime | None = None,
) -> list[SellerMarketStats]:
    runs = _search_runs(session, search_id, end_at=end_at)
    end = end_at or (runs[-1].started_at if runs else None)
    ids = {row.listing_id for row in _matches(session, search_id, end)}
    active = _active_ids(session, runs, end)
    records = (
        session.scalars(select(ListingRecord).where(ListingRecord.id.in_(ids))).all() if ids else []
    )
    prices = _prices_at(session, ids, end)
    drops = _event_listing_counts(session, search_id, "PRICE_DROP")
    groups: dict[tuple[int | None, str | None], list[ListingRecord]] = defaultdict(list)
    for record in records:
        seller = record.seller_user_id or (
            record.profile.wallapop_user_id if record.profile is not None else None
        )
        profile_id = record.profile_id
        groups[(profile_id, seller)].append(record)
    return [
        SellerMarketStats(
            profile_id=key[0],
            seller_external_id=key[1],
            listing_count=len(rows),
            active_count=sum(row.id in active for row in rows),
            median_price=_median(
                [price for row in rows if (price := prices.get(row.id)) is not None]
            ),
            price_drop_count=sum(drops.get(row.id, 0) for row in rows),
        )
        for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), str(item[0])))
    ]


def get_brand_market_stats(
    session: Session, search_id: int, *, end_at: datetime | None = None
) -> list[BrandMarketStats]:
    groups = _attribute_groups(session, search_id, end_at, "brand")
    return [
        BrandMarketStats(
            name,
            len(rows),
            sum(row[0] in _active_ids_for_end(session, search_id, end_at) for row in rows),
            _median([price for _, price, _ in rows if price is not None]),
        )
        for name, rows in sorted(groups.items())
    ]


def get_category_market_stats(
    session: Session, search_id: int, *, end_at: datetime | None = None
) -> list[CategoryMarketStats]:
    groups = _attribute_groups(session, search_id, end_at, "category_id")
    active = _active_ids_for_end(session, search_id, end_at)
    return [
        CategoryMarketStats(
            name,
            next((row[2] for row in rows if row[2]), None),
            len(rows),
            sum(row[0] in active for row in rows),
            _median([price for _, price, _ in rows if price is not None]),
        )
        for name, rows in sorted(groups.items())
    ]


def _attribute_groups(
    session: Session, search_id: int, end: datetime | None, field: str
) -> dict[str, list[tuple[int, Decimal | None, str | None]]]:
    runs = _search_runs(session, search_id, end_at=end)
    ids = {row.listing_id for row in _matches(session, search_id, end)}
    snapshots = _latest_snapshots(session, ids, end)
    result: dict[str, list[tuple[int, Decimal | None, str | None]]] = defaultdict(list)
    for listing_id, snapshot in snapshots.items():
        value = getattr(snapshot, field)
        if value:
            result[str(value)].append((listing_id, snapshot.price, snapshot.category_name))
    del runs
    return result


def _active_ids_for_end(session: Session, search_id: int, end: datetime | None) -> set[int]:
    runs = _search_runs(session, search_id, end_at=end)
    return _active_ids(session, runs, end)


def _search_runs(
    session: Session, search_id: int, *, end_at: datetime | None
) -> list[TrackingRunRecord]:
    statement = select(TrackingRunRecord).where(
        TrackingRunRecord.tracked_search_id == search_id,
        TrackingRunRecord.status == TrackingRunStatus.VALID,
    )
    if end_at is not None:
        statement = statement.where(TrackingRunRecord.started_at <= end_at)
    return list(
        session.scalars(statement.order_by(TrackingRunRecord.started_at, TrackingRunRecord.id))
    )


def _period_runs(
    session: Session, search_id: int, start: datetime | None, end: datetime | None
) -> list[TrackingRunRecord]:
    return [
        run
        for run in _search_runs(session, search_id, end_at=end)
        if _in_period(run.started_at, start, end)
    ]


def _matches(
    session: Session, search_id: int, end: datetime | None
) -> list[SearchListingMatchRecord]:
    statement = select(SearchListingMatchRecord).where(
        SearchListingMatchRecord.tracked_search_id == search_id
    )
    if end is not None:
        statement = statement.where(SearchListingMatchRecord.first_seen_at <= end)
    return list(session.scalars(statement))


def _active_ids(session: Session, runs: list[TrackingRunRecord], end: datetime | None) -> set[int]:
    eligible = [run for run in runs if end is None or _utc(run.started_at) <= _utc(end)]
    if not eligible:
        return set()
    latest = eligible[-1]
    return set(
        session.scalars(
            select(TrackingRunListingRecord.listing_id).where(
                TrackingRunListingRecord.tracking_run_id == latest.id
            )
        )
    )


def _latest_snapshots(
    session: Session, ids: set[int], end: datetime | None
) -> dict[int, ListingSnapshotRecord]:
    if not ids:
        return {}
    statement = select(ListingSnapshotRecord).where(ListingSnapshotRecord.listing_id.in_(ids))
    if end is not None:
        statement = statement.where(ListingSnapshotRecord.observed_at <= end)
    rows = session.scalars(
        statement.order_by(ListingSnapshotRecord.observed_at, ListingSnapshotRecord.id)
    ).all()
    return {row.listing_id: row for row in rows}


def _prices_at(session: Session, ids: set[int], end: datetime | None) -> dict[int, Decimal | None]:
    return {
        listing_id: snapshot.price
        for listing_id, snapshot in _latest_snapshots(session, ids, end).items()
    }


def _removed_transitions(
    session: Session, runs: list[TrackingRunRecord], start: datetime | None, end: datetime | None
) -> dict[int, tuple[datetime, int]]:
    result: dict[int, tuple[datetime, int]] = {}
    previous: set[int] | None = None
    for run in runs:
        current = _active_ids(session, [run], run.started_at)
        if previous is not None and _in_period(run.started_at, start, end):
            for listing_id in previous - current:
                result.setdefault(listing_id, (_utc(run.started_at), run.id))
        previous = current
    return result


def _observed_durations(
    session: Session,
    matches: list[SearchListingMatchRecord],
    removed: dict[int, tuple[datetime, int]],
    end: datetime | None,
) -> list[timedelta]:
    durations = []
    for match in matches:
        finish = removed.get(match.listing_id, (_utc(match.last_seen_at), 0))[0]
        if end is not None and finish > _utc(end):
            finish = _utc(end)
        duration = finish - _utc(match.first_seen_at)
        if duration >= timedelta(0):
            durations.append(duration)
    return durations


def _price_event_counts(
    session: Session, search_id: int, start: datetime | None, end: datetime | None
) -> tuple[int, int]:
    statement = select(TrackingEventRecord.event_type).where(
        TrackingEventRecord.tracked_search_id == search_id,
        TrackingEventRecord.event_type.in_(("PRICE_DROP", "PRICE_INCREASE")),
    )
    if start is not None:
        statement = statement.where(TrackingEventRecord.created_at >= start)
    if end is not None:
        statement = statement.where(TrackingEventRecord.created_at <= end)
    values = list(session.scalars(statement))
    return values.count("PRICE_DROP"), values.count("PRICE_INCREASE")


def _event_listing_counts(session: Session, search_id: int, event_type: str) -> dict[int, int]:
    rows = session.execute(
        select(TrackingEventRecord.listing_id, func.count())
        .where(
            TrackingEventRecord.tracked_search_id == search_id,
            TrackingEventRecord.event_type == event_type,
        )
        .group_by(TrackingEventRecord.listing_id)
    )
    return {listing_id: int(count) for listing_id, count in rows}


def _events_by_bucket(
    session: Session,
    search_id: int,
    event_type: str,
    start: datetime | None,
    end: datetime | None,
    granularity: str,
) -> dict[datetime, int]:
    statement = select(TrackingEventRecord.created_at).where(
        TrackingEventRecord.tracked_search_id == search_id,
        TrackingEventRecord.event_type == event_type,
    )
    if start is not None:
        statement = statement.where(TrackingEventRecord.created_at >= start)
    if end is not None:
        statement = statement.where(TrackingEventRecord.created_at <= end)
    result: defaultdict[datetime, int] = defaultdict(int)
    for value in session.scalars(statement):
        result[_bucket(value, granularity)] += 1
    return result


def _last_run_per_bucket(
    runs: list[TrackingRunRecord], granularity: str
) -> list[TrackingRunRecord]:
    if granularity not in {"daily", "weekly"}:
        raise ValueError("granularity must be daily or weekly")
    selected: dict[datetime, TrackingRunRecord] = {}
    for run in runs:
        selected[_bucket(run.started_at, granularity)] = run
    return [selected[key] for key in sorted(selected)]


def _bucket(value: datetime, granularity: str) -> datetime:
    value = _utc(value)
    if granularity == "daily":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "weekly":
        start = value - timedelta(days=value.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    raise ValueError("granularity must be daily or weekly")


def _price_stats(
    values: Iterable[Decimal | None],
) -> tuple[
    Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None
]:
    prices = sorted(value for value in values if isinstance(value, Decimal))
    if not prices:
        return (None,) * 6
    return (
        _median(prices),
        sum(prices, Decimal("0")) / len(prices),
        _percentile(prices, Decimal("0.25")),
        _percentile(prices, Decimal("0.75")),
        prices[0],
        prices[-1],
    )


def _median(values: list[Decimal]) -> Decimal | None:
    return _percentile(values, Decimal("0.5")) if values else None


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal:
    if len(values) == 1:
        return values[0]
    position = Decimal(len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _decimal_timedelta(values: list[timedelta]) -> timedelta | None:
    if not values:
        return None
    seconds = Decimal(str(median([value.total_seconds() for value in values])))
    return timedelta(seconds=float(seconds))


def _in_period(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    value = _utc(value)
    return (start is None or value >= _utc(start)) and (end is None or value <= _utc(end))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
