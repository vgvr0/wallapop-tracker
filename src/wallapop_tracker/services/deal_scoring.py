"""Deterministic, read-only deal scoring for a listing within one search."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wallapop_tracker.domain.deal_scoring import (
    DealScore,
    DealScoreReason,
    DealScoreStatus,
    DealScoringPolicy,
)
from wallapop_tracker.reporting.market import get_market_summary
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PossibleRelistingRecord,
    ProfileSnapshotRecord,
    SearchListingMatchRecord,
    TrackingEventRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class DealScoringService:
    """Calculate a contextual score without writing a score or ranking table."""

    def __init__(self, session: Session, policy: DealScoringPolicy | None = None) -> None:
        self.session = session
        self.policy = policy or DealScoringPolicy()

    def score_listing(self, listing_id: int, search_id: int) -> DealScore:
        now = datetime.now(UTC)
        listing = self.session.get(ListingRecord, listing_id)
        match = self.session.get(SearchListingMatchRecord, (search_id, listing_id))
        if listing is None or match is None:
            return self._insufficient(listing_id, search_id, now, "listing_not_observed")

        snapshot = self._latest_snapshot(listing_id)
        if snapshot is None or snapshot.price is None:
            return self._insufficient(listing_id, search_id, now, "listing_price_missing")

        market = get_market_summary(
            self.session, search_id, end_at=now, exclude_listing_id=listing_id
        )
        history_runs = self._history_runs(search_id)
        if (
            market.priced_listing_count < self.policy.minimum_comparables
            or market.median_price is None
            or (
                self.policy.minimum_history_days > 0
                and (
                    not history_runs
                    or (now - _utc(history_runs[0].started_at)).total_seconds()
                    < self.policy.minimum_history_days * 86400
                )
            )
        ):
            return self._insufficient(listing_id, search_id, now, "insufficient_comparables")

        reasons: list[DealScoreReason] = []
        score = 50.0
        median = market.median_price
        discount = (median - snapshot.price) / median if median else Decimal("0")
        price_points = _clamp(
            float(discount / self.policy.discount_scale) * self.policy.price_weight,
            -self.policy.price_weight,
            self.policy.price_weight,
        )
        score += price_points
        reasons.append(
            DealScoreReason(
                "price_below_median" if discount >= 0 else "price_above_median",
                round(price_points, 2),
                discount,
                f"Price {abs(discount):.1%} {'below' if discount >= 0 else 'above'} market median",
            )
        )

        if market.p25_price is not None and snapshot.price <= market.p25_price:
            score += self.policy.price_weight * 12 / 55
            reasons.append(
                DealScoreReason(
                    "price_below_p25",
                    round(self.policy.price_weight * 12 / 55, 2),
                    snapshot.price,
                    "Price is at or below market P25",
                )
            )

        age = max(timedelta(0), now - _utc(match.first_seen_at))
        freshness = self._freshness(age)
        if freshness:
            score += freshness
            reasons.append(
                DealScoreReason(
                    "fresh_listing",
                    freshness,
                    age.total_seconds(),
                    f"First seen {self._age_text(age)} ago",
                )
            )

        drop_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(TrackingEventRecord)
                .where(
                    TrackingEventRecord.tracked_search_id == search_id,
                    TrackingEventRecord.listing_id == listing_id,
                    TrackingEventRecord.event_type == "PRICE_DROP",
                )
            )
            or 0
        )
        if drop_count:
            history_points = min(self.policy.history_weight, drop_count * 3.0)
            score += history_points
            reasons.append(
                DealScoreReason(
                    "price_drop",
                    history_points,
                    drop_count,
                    f"{drop_count} observed price drop(s)",
                )
            )

        seller_points, seller_description = self._seller_points(listing)
        if seller_points:
            score += seller_points
            reasons.append(
                DealScoreReason(
                    "seller_history",
                    seller_points,
                    seller_description or "seller metrics",
                    seller_description or "Seller metrics available",
                )
            )

        relisting = self.session.scalar(
            select(PossibleRelistingRecord.id)
            .where(PossibleRelistingRecord.current_listing_id == listing_id)
            .limit(1)
        )
        if relisting is not None:
            reasons.append(
                DealScoreReason(
                    "possible_relisting",
                    0.0,
                    True,
                    "Listing has a possible relisting signal",
                )
            )

        confidence = self._confidence(market.priced_listing_count, len(history_runs))
        reasons.append(
            DealScoreReason(
                "market_depth",
                0.0,
                market.priced_listing_count,
                f"{market.priced_listing_count} priced comparables; confidence {confidence:.0%}",
            )
        )
        return DealScore(
            listing_id,
            search_id,
            int(round(_clamp(score, 0, 100))),
            confidence,
            DealScoreStatus.SCORED,
            tuple(reasons),
            now,
        )

    def score_search(self, search_id: int, *, limit: int | None = None) -> list[DealScore]:
        runs = list(
            self.session.scalars(
                select(TrackingRunRecord)
                .where(
                    TrackingRunRecord.tracked_search_id == search_id,
                    TrackingRunRecord.status == TrackingRunStatus.VALID,
                )
                .order_by(TrackingRunRecord.started_at, TrackingRunRecord.id)
            )
        )
        if not runs:
            return []
        listing_ids = list(
            self.session.scalars(
                select(TrackingRunListingRecord.listing_id).where(
                    TrackingRunListingRecord.tracking_run_id == runs[-1].id
                )
            )
        )
        scores = [self.score_listing(listing_id, search_id) for listing_id in listing_ids]
        scores.sort(key=lambda item: (item.score is None, -(item.score or 0), item.listing_id))
        return scores[:limit] if limit is not None else scores

    def _latest_snapshot(self, listing_id: int) -> ListingSnapshotRecord | None:
        return self.session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == listing_id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )

    def _seller_points(self, listing: ListingRecord) -> tuple[float, str | None]:
        if listing.profile_id is None:
            return 0.0, None
        snapshot = self.session.scalar(
            select(ProfileSnapshotRecord)
            .where(ProfileSnapshotRecord.profile_id == listing.profile_id)
            .order_by(ProfileSnapshotRecord.observed_at.desc(), ProfileSnapshotRecord.id.desc())
            .limit(1)
        )
        if snapshot is None or snapshot.review_count is None:
            return 0.0, None
        points = 0.0
        if snapshot.review_count >= 100:
            points += self.policy.seller_weight * 0.6
        elif snapshot.review_count >= 20:
            points += self.policy.seller_weight * 0.3
        if snapshot.rating is not None and snapshot.rating >= Decimal("4.5"):
            points += self.policy.seller_weight * 0.4
        if points:
            rating = f" rating {snapshot.rating:.1f}" if snapshot.rating is not None else ""
            return points, f"Seller has {snapshot.review_count} reviews{rating}"
        return 0.0, None

    def _history_runs(self, search_id: int) -> list[TrackingRunRecord]:
        return list(
            self.session.scalars(
                select(TrackingRunRecord)
                .where(
                    TrackingRunRecord.tracked_search_id == search_id,
                    TrackingRunRecord.status == TrackingRunStatus.VALID,
                )
                .order_by(TrackingRunRecord.started_at, TrackingRunRecord.id)
            )
        )

    def _confidence(self, comparables: int, history_runs: int) -> float:
        depth = min(1.0, comparables / 20)
        history = min(1.0, history_runs / 5)
        return round(0.7 * depth + 0.3 * history, 2)

    def _freshness(self, age: timedelta) -> float:
        if age < timedelta(minutes=self.policy.freshness_minutes):
            return self.policy.freshness_weight
        if age < timedelta(hours=self.policy.freshness_hours):
            return self.policy.freshness_weight * 0.6
        if age < timedelta(days=self.policy.freshness_days):
            return self.policy.freshness_weight * 0.25
        return 0.0

    @staticmethod
    def _age_text(age: timedelta) -> str:
        seconds = int(age.total_seconds())
        if seconds < 3600:
            return f"{max(1, seconds // 60)} minutes"
        return f"{seconds // 3600} hours"

    @staticmethod
    def _insufficient(listing_id: int, search_id: int, now: datetime, reason: str) -> DealScore:
        return DealScore(
            listing_id,
            search_id,
            None,
            0.0,
            DealScoreStatus.INSUFFICIENT_DATA,
            (DealScoreReason(reason, 0.0, None, "Not enough reliable data to calculate a score"),),
            now,
        )
