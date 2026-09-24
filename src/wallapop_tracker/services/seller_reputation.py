"""Read-only, descriptive seller reputation intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from wallapop_tracker.storage.models import ProfileRecord, ProfileSnapshotRecord


@dataclass(frozen=True)
class SellerReputationInsight:
    """Observable seller metrics and relative context; never a trust or fraud score."""

    profile_id: int
    generated_at: datetime
    rating: float | None
    reviews: int | None
    sales: int | None
    published: int | None
    sold: int | None
    reports_received: int | None
    rating_1_count: int | None
    rating_2_count: int | None
    rating_3_count: int | None
    rating_4_count: int | None
    rating_5_count: int | None
    low_rating_count: int | None
    low_rating_ratio: float | None
    reports_per_100_sales: float | None
    reports_per_100_reviews: float | None
    reports_delta_30d: int | None
    reports_delta_90d: int | None
    rating_delta_30d: float | None
    review_growth_30d: int | None
    sales_growth_30d: int | None
    peer_median_reports: float | None
    peer_percentile: float | None
    peer_sample_size: int
    interpretation: str | None
    confidence: str
    data_quality: str
    warnings: list[str]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _change(current: int | float | None, previous: int | float | None) -> int | float | None:
    if current is None or previous is None:
        return None
    return current - previous


def _int_change(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return current - previous


def _rating_count(review_count: int | None, percentage: int | None) -> int | None:
    if review_count is None or percentage is None:
        return None
    return round(review_count * percentage / 100)


def _latest_snapshots(session: Session) -> list[tuple[ProfileSnapshotRecord, str | None]]:
    row_number = (
        func.row_number()
        .over(
            partition_by=ProfileSnapshotRecord.profile_id,
            order_by=(desc(ProfileSnapshotRecord.observed_at), desc(ProfileSnapshotRecord.id)),
        )
        .label("row_number")
    )
    latest = select(ProfileSnapshotRecord.id.label("snapshot_id"), row_number).subquery()
    return [
        (row[0], row[1])
        for row in session.execute(
            select(ProfileSnapshotRecord, ProfileRecord.seller_type)
            .join(ProfileRecord, ProfileRecord.id == ProfileSnapshotRecord.profile_id)
            .join(latest, latest.c.snapshot_id == ProfileSnapshotRecord.id)
            .where(latest.c.row_number == 1)
        ).all()
    ]


def _historical_snapshot(
    snapshots: list[ProfileSnapshotRecord], cutoff: datetime
) -> ProfileSnapshotRecord | None:
    eligible = [snapshot for snapshot in snapshots if _utc(snapshot.observed_at) <= cutoff]
    return max(
        eligible, key=lambda snapshot: (_utc(snapshot.observed_at), snapshot.id), default=None
    )


def _peer_matches(
    target: ProfileSnapshotRecord,
    target_seller_type: str | None,
    peers: list[tuple[ProfileSnapshotRecord, str | None]],
) -> list[ProfileSnapshotRecord]:
    result: list[ProfileSnapshotRecord] = []
    target_sales = target.sales_count
    for peer, seller_type in peers:
        if peer.id == target.id or peer.reports_received is None:
            continue
        if target_seller_type is not None and seller_type != target_seller_type:
            continue
        if target_sales is not None and target_sales > 0:
            if peer.sales_count is None:
                continue
            lower = max(1, target_sales // 2)
            upper = max(lower, target_sales * 2)
            if not lower <= peer.sales_count <= upper:
                continue
        result.append(peer)
    return result


def _interpretation(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    if percentile < 25:
        return "below_peer_range"
    if percentile < 75:
        return "within_peer_range"
    if percentile < 90:
        return "above_peer_range"
    return "very_high_relative_to_peers"


def build_seller_reputation(
    session: Session,
    profile_id: int,
    *,
    now: datetime | None = None,
    min_peer_sample: int = 20,
) -> SellerReputationInsight:
    """Build an insight only from persisted snapshots; never performs network I/O."""

    generated_at = _utc(now or datetime.now(UTC))
    profile = session.get(ProfileRecord, profile_id)
    if profile is None:
        raise ValueError(f"Profile not found: {profile_id}")

    own_snapshots = list(
        session.scalars(
            select(ProfileSnapshotRecord)
            .where(ProfileSnapshotRecord.profile_id == profile_id)
            .order_by(ProfileSnapshotRecord.observed_at, ProfileSnapshotRecord.id)
        )
    )
    current = own_snapshots[-1] if own_snapshots else None
    warnings: list[str] = []
    if current is None:
        warnings.append("No profile snapshot is available")

    def value(name: str) -> Any:
        return getattr(current, name) if current is not None else None

    reviews = value("review_count")
    reports = value("reports_received")
    low_rating_count = None
    rating_counts = [_rating_count(reviews, value(f"rating_{star}_pct")) for star in range(1, 6)]
    if (
        rating_counts[0] is not None
        and rating_counts[1] is not None
        and rating_counts[2] is not None
    ):
        low_rating_count = rating_counts[0] + rating_counts[1] + rating_counts[2]
    if reports is None:
        warnings.append("reports_received is unavailable")
    if value("rating") is None:
        warnings.append("rating is unavailable")

    baseline_30 = _historical_snapshot(own_snapshots, generated_at - timedelta(days=30))
    baseline_90 = _historical_snapshot(own_snapshots, generated_at - timedelta(days=90))
    if baseline_30 is None:
        warnings.append("No snapshot older than 30 days is available")
    if baseline_90 is None:
        warnings.append("No snapshot older than 90 days is available")

    peer_median_reports = None
    peer_percentile = None
    peer_sample_size = 0
    if current is not None:
        peer_rows = _peer_matches(current, profile.seller_type, _latest_snapshots(session))
        report_values = sorted(
            peer.reports_received for peer in peer_rows if peer.reports_received is not None
        )
        peer_sample_size = len(report_values)
        if peer_sample_size >= min_peer_sample and reports is not None:
            peer_median_reports = float(median(report_values))
            peer_percentile = round(
                sum(value <= reports for value in report_values) / peer_sample_size * 100, 1
            )
        elif peer_sample_size < min_peer_sample:
            warnings.append(f"Only {peer_sample_size} comparable sellers are available")

    data_quality = "sufficient"
    if current is None or reports is None:
        data_quality = "partial"
    if peer_sample_size < min_peer_sample:
        data_quality = "insufficient_data"
    confidence = "sufficient" if data_quality == "sufficient" else data_quality

    return SellerReputationInsight(
        profile_id=profile_id,
        generated_at=generated_at,
        rating=float(value("rating")) if value("rating") is not None else None,
        reviews=reviews,
        sales=value("sales_count"),
        published=value("published_count"),
        sold=value("sold_count"),
        reports_received=reports,
        rating_1_count=rating_counts[0],
        rating_2_count=rating_counts[1],
        rating_3_count=rating_counts[2],
        rating_4_count=rating_counts[3],
        rating_5_count=rating_counts[4],
        low_rating_count=low_rating_count,
        low_rating_ratio=(
            low_rating_count / reviews if low_rating_count is not None and reviews else None
        ),
        reports_per_100_sales=(
            reports * 100 / value("sales_count")
            if reports is not None and value("sales_count")
            else None
        ),
        reports_per_100_reviews=(
            reports * 100 / reviews if reports is not None and reviews else None
        ),
        reports_delta_30d=_int_change(
            reports, baseline_30.reports_received if baseline_30 else None
        ),
        reports_delta_90d=_int_change(
            reports, baseline_90.reports_received if baseline_90 else None
        ),
        rating_delta_30d=_change(
            float(value("rating")) if value("rating") is not None else None,
            float(baseline_30.rating) if baseline_30 and baseline_30.rating is not None else None,
        ),
        review_growth_30d=_int_change(reviews, baseline_30.review_count if baseline_30 else None),
        sales_growth_30d=_int_change(
            value("sales_count"), baseline_30.sales_count if baseline_30 else None
        ),
        peer_median_reports=peer_median_reports,
        peer_percentile=peer_percentile,
        peer_sample_size=peer_sample_size,
        interpretation=_interpretation(peer_percentile),
        confidence=confidence,
        data_quality=data_quality,
        warnings=warnings,
    )
