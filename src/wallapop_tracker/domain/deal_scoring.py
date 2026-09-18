"""Domain objects for deterministic, explainable deal scoring."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class DealScoreStatus(StrEnum):
    SCORED = "scored"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class DealScoreReason:
    name: str
    contribution: float
    value: str | Decimal | float | int | bool | None
    description: str


@dataclass(frozen=True)
class DealScore:
    listing_id: int
    search_id: int
    score: int | None
    confidence: float
    status: DealScoreStatus
    reasons: tuple[DealScoreReason, ...]
    calculated_at: datetime


@dataclass(frozen=True)
class DealScoringPolicy:
    """Stable scoring policy; all monetary calculations remain Decimal based."""

    minimum_comparables: int = 3
    minimum_history_days: int = 0
    price_weight: float = 55.0
    freshness_weight: float = 15.0
    history_weight: float = 10.0
    seller_weight: float = 10.0
    freshness_minutes: int = 15
    freshness_hours: int = 1
    freshness_days: int = 1
    discount_scale: Decimal = Decimal("0.30")
