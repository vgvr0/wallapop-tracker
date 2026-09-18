"""Read-only historical queries for the persisted tracking history."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wallapop_tracker.domain.relisting import RelistingCandidate, RelistingReason
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PossibleRelistingRecord,
    PresenceState,
    ProfileSnapshotRecord,
    SearchListingMatchRecord,
    TrackingEventRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


@dataclass(frozen=True)
class SearchTrackingMetrics:
    search_id: int
    runs: int
    listings_discovered: int
    matched_listings: int
    new_listings: int
    price_changes: int
    alerts_generated: int
    duplicates_suppressed: int


@dataclass(frozen=True)
class InventoryListing:
    listing_id: int
    wallapop_item_id: str
    title: str | None
    price: Decimal | None


@dataclass(frozen=True)
class InventoryPoint:
    run_id: int
    observed_at: datetime
    count: int


@dataclass(frozen=True)
class PricePoint:
    run_id: int
    observed_at: datetime
    price: Decimal | None
    presence_state: PresenceState


@dataclass(frozen=True)
class PresencePoint:
    run_id: int
    observed_at: datetime
    present: bool


@dataclass(frozen=True)
class ProfileMetricsPoint:
    run_id: int
    observed_at: datetime
    rating: Decimal | None
    review_count: int | None
    published_count: int | None
    purchases_count: int | None
    sales_count: int | None
    sold_count: int | None
    reports_count: int | None


@dataclass(frozen=True)
class ApproxActiveDuration:
    listing_id: int
    first_observed_run_id: int
    first_observed_at: datetime
    last_observed_run_id: int
    last_observed_at: datetime
    approx_active_duration: timedelta


def _utc(value: datetime) -> datetime:
    """Make SQLite's naive datetime results comparable to aware inputs."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _valid_runs(session: Session, profile_id: int) -> list[TrackingRunRecord]:
    return list(
        session.scalars(
            select(TrackingRunRecord)
            .where(
                TrackingRunRecord.profile_id == profile_id,
                TrackingRunRecord.status == TrackingRunStatus.VALID,
            )
            .order_by(TrackingRunRecord.started_at, TrackingRunRecord.id)
        )
    )


def _presence_by_run(
    session: Session, run_ids: list[int], listing_id: int | None = None
) -> dict[int, set[int]]:
    if not run_ids:
        return {}
    statement = select(TrackingRunListingRecord).where(
        TrackingRunListingRecord.tracking_run_id.in_(run_ids)
    )
    if listing_id is not None:
        statement = statement.where(TrackingRunListingRecord.listing_id == listing_id)
    rows = session.scalars(statement).all()
    result: dict[int, set[int]] = {}
    for row in rows:
        result.setdefault(row.tracking_run_id, set()).add(row.listing_id)
    return result


def get_current_inventory(session: Session, profile_id: int) -> list[InventoryListing]:
    """Return listings present in the latest valid run, using run presence as authority."""
    runs = _valid_runs(session, profile_id)
    if not runs:
        return []
    present_ids = _presence_by_run(session, [runs[-1].id]).get(runs[-1].id, set())
    if not present_ids:
        return []
    rows = session.scalars(
        select(ListingRecord).where(
            ListingRecord.profile_id == profile_id, ListingRecord.id.in_(present_ids)
        ).order_by(ListingRecord.id)
    ).all()
    snapshots = _latest_listing_snapshots(session, list(present_ids), runs[-1].started_at)
    result = []
    for row in rows:
        title, price = snapshots.get(row.id, (None, None))
        result.append(
            InventoryListing(
                row.id, row.external_id or row.wallapop_item_id or "", title, price
            )
        )
    return result


def get_inventory_history(session: Session, profile_id: int) -> list[InventoryPoint]:
    runs = _valid_runs(session, profile_id)
    presence = _presence_by_run(session, [run.id for run in runs])
    return [
        InventoryPoint(run.id, _utc(run.started_at), len(presence.get(run.id, set())))
        for run in runs
    ]


def get_new_listings_between_runs(session: Session, run_a_id: int, run_b_id: int) -> list[int]:
    a, b = _presence_sets(session, run_a_id, run_b_id)
    return sorted(b - a)


def get_removed_listings_between_runs(session: Session, run_a_id: int, run_b_id: int) -> list[int]:
    a, b = _presence_sets(session, run_a_id, run_b_id)
    return sorted(a - b)


def _presence_sets(session: Session, run_a_id: int, run_b_id: int) -> tuple[set[int], set[int]]:
    rows = _presence_by_run(session, [run_a_id, run_b_id])
    return rows.get(run_a_id, set()), rows.get(run_b_id, set())


def _latest_listing_snapshots(
    session: Session, listing_ids: list[int], observed_at: datetime
) -> dict[int, tuple[str | None, Decimal | None]]:
    if not listing_ids:
        return {}
    rows = session.scalars(
        select(ListingSnapshotRecord)
        .where(
            ListingSnapshotRecord.listing_id.in_(listing_ids),
            ListingSnapshotRecord.observed_at <= observed_at,
        )
        .order_by(ListingSnapshotRecord.observed_at, ListingSnapshotRecord.id)
    ).all()
    latest: dict[int, tuple[str | None, Decimal | None]] = {}
    for row in rows:
        latest[row.listing_id] = (row.title, row.price)
    return latest


def get_price_history(session: Session, listing_id: int) -> list[PricePoint]:
    listing = session.get(ListingRecord, listing_id)
    if listing is None or listing.profile_id is None:
        return []
    runs = _valid_runs(session, listing.profile_id)
    presence = _presence_by_run(session, [run.id for run in runs], listing_id)
    snapshots = session.scalars(
        select(ListingSnapshotRecord)
        .where(ListingSnapshotRecord.listing_id == listing_id)
        .order_by(ListingSnapshotRecord.observed_at, ListingSnapshotRecord.id)
    ).all()
    points: list[PricePoint] = []
    latest_price: Decimal | None = None
    snapshot_index = 0
    previous: tuple[Decimal | None, PresenceState] | None = None
    for run in runs:
        while (
            snapshot_index < len(snapshots)
            and _utc(snapshots[snapshot_index].observed_at) <= _utc(run.started_at)
        ):
            latest_price = snapshots[snapshot_index].price
            snapshot_index += 1
        state = (
            PresenceState.ACTIVE
            if listing_id in presence.get(run.id, set())
            else PresenceState.REMOVED
        )
        current = (latest_price, state)
        if current != previous:
            points.append(PricePoint(run.id, _utc(run.started_at), latest_price, state))
            previous = current
    return points


def get_presence_history(session: Session, listing_id: int) -> list[PresencePoint]:
    listing = session.get(ListingRecord, listing_id)
    if listing is None or listing.profile_id is None:
        return []
    runs = _valid_runs(session, listing.profile_id)
    presence = _presence_by_run(session, [run.id for run in runs], listing_id)
    return [
        PresencePoint(run.id, _utc(run.started_at), listing_id in presence.get(run.id, set()))
        for run in runs
    ]


def get_profile_metrics_history(session: Session, profile_id: int) -> list[ProfileMetricsPoint]:
    runs = _valid_runs(session, profile_id)
    snapshots = session.scalars(
        select(ProfileSnapshotRecord)
        .where(ProfileSnapshotRecord.profile_id == profile_id)
        .order_by(ProfileSnapshotRecord.observed_at, ProfileSnapshotRecord.id)
    ).all()
    points: list[ProfileMetricsPoint] = []
    latest: ProfileSnapshotRecord | None = None
    index = 0
    for run in runs:
        while (
            index < len(snapshots)
            and _utc(snapshots[index].observed_at) <= _utc(run.started_at)
        ):
            latest = snapshots[index]
            index += 1
        points.append(
            ProfileMetricsPoint(
                run.id, _utc(run.started_at),
                latest.rating if latest else None,
                latest.review_count if latest else None,
                latest.published_count if latest else None,
                latest.purchases_count if latest else None,
                latest.sales_count if latest else None,
                latest.sold_count if latest else None,
                latest.reports_count if latest else None,
            )
        )
    return points


def get_approx_active_duration(session: Session, listing_id: int) -> ApproxActiveDuration | None:
    history = get_presence_history(session, listing_id)
    active = [point for point in history if point.present]
    if not active:
        return None
    first, last = active[0], active[-1]
    return ApproxActiveDuration(
        listing_id, first.run_id, first.observed_at, last.run_id, last.observed_at,
        last.observed_at - first.observed_at,
    )


def get_search_tracking_metrics(session: Session, search_id: int) -> SearchTrackingMetrics:
    """Return persisted operational metrics for one tracked search."""
    runs = list(
        session.scalars(
            select(TrackingRunRecord).where(TrackingRunRecord.tracked_search_id == search_id)
        )
    )
    run_ids = [run.id for run in runs]
    matched = session.scalar(
        select(func.count())
        .select_from(SearchListingMatchRecord)
        .where(SearchListingMatchRecord.tracked_search_id == search_id)
    ) or 0
    alerts = session.scalar(
        select(func.count())
        .select_from(TrackingEventRecord)
        .where(TrackingEventRecord.tracked_search_id == search_id)
    ) or 0
    discovered = (
        session.scalar(
            select(func.count(func.distinct(TrackingRunListingRecord.listing_id))).where(
                TrackingRunListingRecord.tracking_run_id.in_(run_ids)
            )
        )
        if run_ids
        else 0
    )
    return SearchTrackingMetrics(
        search_id,
        len(runs),
        int(discovered or 0),
        int(matched),
        sum(run.new_listings or 0 for run in runs),
        sum(run.price_changes or 0 for run in runs),
        int(alerts),
        sum(run.duplicates_suppressed or 0 for run in runs),
    )


def _relisting_candidate(record: PossibleRelistingRecord) -> RelistingCandidate:
    values = json.loads(record.reasons_json)
    reasons = tuple(
        RelistingReason(
            name=value["name"],
            value=value["value"],
            contribution=float(value["contribution"]),
        )
        for value in values
    )
    return RelistingCandidate(
        previous_listing_id=record.previous_listing_id,
        current_listing_id=record.current_listing_id,
        score=float(record.score),
        reasons=reasons,
    )


def get_possible_relistings(
    session: Session,
    *,
    min_score: Decimal | None = None,
    listing_id: int | None = None,
) -> list[RelistingCandidate]:
    statement = select(PossibleRelistingRecord)
    if min_score is not None:
        statement = statement.where(PossibleRelistingRecord.score >= min_score)
    if listing_id is not None:
        statement = statement.where(
            (PossibleRelistingRecord.previous_listing_id == listing_id)
            | (PossibleRelistingRecord.current_listing_id == listing_id)
        )
    rows = session.scalars(
        statement.order_by(
            PossibleRelistingRecord.score.desc(), PossibleRelistingRecord.detected_at.desc()
        )
    ).all()
    return [_relisting_candidate(row) for row in rows]


def get_relisting_candidates_for_listing(
    session: Session, listing_id: int
) -> list[RelistingCandidate]:
    return get_possible_relistings(session, listing_id=listing_id)
