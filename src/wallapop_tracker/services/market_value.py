"""Deterministic, explainable market value estimates from stored observations."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.storage.models import ListingRecord, ListingSnapshotRecord

STOP = {"de", "con", "para", "el", "la", "los", "las", "un", "una", "nuevo", "vendo"}
THRESHOLD = Decimal("0.45")
IQR_FACTOR = Decimal("1.5")
DEFAULT_LIMIT = 1000


class MarketConfidence(StrEnum):
    INSUFFICIENT = "insufficient"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ComparableListing:
    listing_id: int
    price: Decimal
    title: str | None
    brand: str | None
    condition: str | None
    observed_at: datetime
    similarity_score: Decimal
    matched_signals: tuple[str, ...]


@dataclass(frozen=True)
class SelectionSummary:
    candidate_count: int
    accepted_count: int
    outlier_count: int


@dataclass(frozen=True)
class MarketValueEstimate:
    listing_id: int
    current_price: Decimal | None
    median_price: Decimal | None
    p25_price: Decimal | None
    p75_price: Decimal | None
    iqr: Decimal | None
    sample_size: int
    discount_vs_median: Decimal | None
    percentile: float | None
    confidence: float
    confidence_level: MarketConfidence
    comparables: tuple[ComparableListing, ...]
    selection_summary: SelectionSummary
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["confidence_level"] = self.confidence_level.value
        value["comparables"] = [asdict(item) for item in self.comparables]
        value["selection_summary"] = asdict(self.selection_summary)
        return value


def _norm(value: str | None) -> str:
    return " ".join(re.findall(r"[\w]+", (value or "").casefold()))


def _tokens(value: str | None) -> set[str]:
    return {token for token in _norm(value).split() if token not in STOP and len(token) > 1}


def _latest(session: Session, cutoff: datetime, limit: int) -> dict[int, ListingSnapshotRecord]:
    rows = session.scalars(
        select(ListingSnapshotRecord)
        .where(ListingSnapshotRecord.observed_at >= cutoff)
        .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
        .limit(limit)
    ).all()
    result: dict[int, ListingSnapshotRecord] = {}
    for row in rows:
        result.setdefault(row.listing_id, row)
    return result


def _score(
    target: ListingSnapshotRecord, candidate: ListingSnapshotRecord
) -> tuple[Decimal, tuple[str, ...]]:
    score = Decimal("0")
    signals: list[str] = []
    if target.category_id and candidate.category_id == target.category_id:
        score += Decimal("0.25")
        signals.append("same category")
    if target.brand and candidate.brand and _norm(target.brand) == _norm(candidate.brand):
        score += Decimal("0.25")
        signals.append("same brand")
    overlap = _tokens(target.title) & _tokens(candidate.title)
    union = _tokens(target.title) | _tokens(candidate.title)
    if overlap and union:
        score += Decimal("0.30") * Decimal(len(overlap)) / Decimal(len(union))
        signals.append("title token overlap")
    condition = target.condition_code or target.condition
    other_condition = candidate.condition_code or candidate.condition
    if condition and other_condition and _norm(condition) == _norm(other_condition):
        score += Decimal("0.10")
        signals.append("same condition")
    age_days = max(0, (datetime.now(UTC) - candidate.observed_at).days)
    score += Decimal("0.10") * max(Decimal("0"), Decimal("1") - Decimal(age_days) / Decimal("30"))
    if age_days <= 30:
        signals.append("recent observation")
    return min(score, Decimal("1")), tuple(signals)


def _quantile(values: list[Decimal], fraction: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _percentile(price: Decimal, values: list[Decimal]) -> float:
    return sum(value < price for value in values) / len(values)


class MarketValueService:
    def __init__(self, session: Session, *, candidate_limit: int = DEFAULT_LIMIT) -> None:
        self.session, self.candidate_limit = session, candidate_limit

    def estimate(
        self, listing_id: int, *, window: timedelta = timedelta(days=30), limit: int | None = None
    ) -> MarketValueEstimate:
        target = self.session.get(ListingRecord, listing_id)
        if target is None:
            raise ValueError(f"Unknown listing: {listing_id}")
        now = datetime.now(UTC)
        snapshots = _latest(self.session, now - window, limit or self.candidate_limit)
        target_snapshot = snapshots.get(listing_id)
        if target_snapshot is None:
            target_snapshot = self.session.scalars(
                select(ListingSnapshotRecord)
                .where(ListingSnapshotRecord.listing_id == listing_id)
                .order_by(ListingSnapshotRecord.observed_at.desc())
            ).first()
        if target_snapshot is None:
            raise ValueError(f"Listing has no snapshot: {listing_id}")
        candidates: list[ComparableListing] = []
        for candidate_id, snapshot in snapshots.items():
            if (
                candidate_id == listing_id
                or snapshot.price is None
                or snapshot.price <= 0
                or (
                    target_snapshot.currency
                    and snapshot.currency
                    and target_snapshot.currency != snapshot.currency
                )
            ):
                continue
            score, signals = _score(target_snapshot, snapshot)
            if score >= THRESHOLD:
                candidates.append(
                    ComparableListing(
                        candidate_id,
                        snapshot.price,
                        snapshot.title,
                        snapshot.brand,
                        snapshot.condition_code or snapshot.condition,
                        snapshot.observed_at,
                        score,
                        signals,
                    )
                )
        candidates.sort(key=lambda item: (-item.similarity_score, item.listing_id))
        raw = [item.price for item in candidates]
        outliers: set[int] = set()
        iqr: Decimal | None = None
        if len(raw) >= 4:
            q1, q3 = _quantile(raw, Decimal("0.25")), _quantile(raw, Decimal("0.75"))
            iqr = q3 - q1
            low, high = q1 - IQR_FACTOR * iqr, q3 + IQR_FACTOR * iqr
            outliers = {
                item.listing_id for item in candidates if item.price < low or item.price > high
            }
        accepted = [item for item in candidates if item.listing_id not in outliers]
        prices = [item.price for item in accepted]
        med = median(prices) if prices else None
        p25: Decimal | None
        p75: Decimal | None
        p25, p75 = (
            (_quantile(prices, Decimal("0.25")), _quantile(prices, Decimal("0.75")))
            if prices
            else (None, None)
        )
        iqr = p75 - p25 if p25 is not None and p75 is not None else None
        current = target_snapshot.price
        discount = (current - med) / med if current is not None and med and med != 0 else None
        avg_sim = (
            float(sum(item.similarity_score for item in accepted) / len(accepted))
            if accepted
            else 0
        )
        confidence = min(
            1.0,
            (min(len(accepted), 15) / 15) * 0.45
            + avg_sim * 0.35
            + (0.20 if iqr is not None and med and iqr / med <= Decimal("0.35") else 0),
        )
        level = (
            MarketConfidence.INSUFFICIENT
            if len(accepted) < 3
            else MarketConfidence.LOW
            if len(accepted) < 6
            else MarketConfidence.MEDIUM
            if len(accepted) < 15
            else MarketConfidence.HIGH
        )
        warnings = ("fewer than 3 accepted comparables",) if len(accepted) < 3 else ()
        return MarketValueEstimate(
            listing_id,
            current,
            med,
            p25,
            p75,
            iqr,
            len(accepted),
            discount,
            _percentile(current, prices) if current is not None and prices else None,
            confidence,
            level,
            tuple(accepted[: limit or 100]),
            SelectionSummary(len(candidates), len(accepted), len(outliers)),
            warnings,
        )
