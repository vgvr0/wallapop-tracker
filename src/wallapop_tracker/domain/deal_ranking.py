"""Explainable, deterministic aggregate deal ranking models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RankingConfidence(StrEnum):
    INSUFFICIENT = "insufficient"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RankingComponent:
    name: str
    raw_value: float | None
    normalized_value: float | None
    weight: float
    contribution: float
    available: bool
    reason: str


@dataclass(frozen=True)
class DealRankResult:
    listing_id: int
    overall_score: float
    deal_score: float | None
    market_score: float | None
    semantic_score: float | None
    risk_penalty: float
    confidence: float
    confidence_level: RankingConfidence
    components: tuple[RankingComponent, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "listing_id": self.listing_id,
            "overall_score": self.overall_score,
            "deal_score": self.deal_score,
            "market_score": self.market_score,
            "semantic_score": self.semantic_score,
            "risk_penalty": self.risk_penalty,
            "confidence": self.confidence,
            "confidence_level": self.confidence_level.value,
            "components": [
                {
                    "name": c.name,
                    "available": c.available,
                    "raw_value": c.raw_value,
                    "normalized_value": c.normalized_value,
                    "effective_weight": c.weight,
                    "contribution": c.contribution,
                    "reason": c.reason,
                }
                for c in self.components
            ],
            "warnings": list(self.warnings),
        }
