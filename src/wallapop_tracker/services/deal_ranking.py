"""Read-only aggregate ranking; AI assessments are inputs, never executors."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from wallapop_tracker.ai.hashing import build_analysis_input_hash
from wallapop_tracker.ai.storage import ListingAIRepository
from wallapop_tracker.domain.deal_ranking import (
    DealRankResult,
    RankingComponent,
    RankingConfidence,
)
from wallapop_tracker.services.deal_scoring import DealScoringService
from wallapop_tracker.services.market_value import (
    MarketConfidence,
    MarketValueEstimate,
    MarketValueService,
)
from wallapop_tracker.storage.models import ListingRecord, SearchListingMatchRecord


@dataclass(frozen=True)
class RankingConfig:
    deal_weight: float = 0.45
    market_weight: float = 0.30
    semantic_weight: float = 0.25
    max_risk_penalty: float = 25.0
    insufficient_confidence: float = 0.25
    low_confidence: float = 0.50
    medium_confidence: float = 0.75

    def __post_init__(self) -> None:
        if min(self.deal_weight, self.market_weight, self.semantic_weight) < 0:
            raise ValueError("ranking weights must be non-negative")
        if self.deal_weight + self.market_weight + self.semantic_weight <= 0:
            raise ValueError("at least one ranking weight must be positive")
        if self.max_risk_penalty < 0:
            raise ValueError("max_risk_penalty must be non-negative")


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


class DealRankingService:
    def __init__(self, session: Session, config: RankingConfig | None = None) -> None:
        self.session = session
        self.config = config or RankingConfig()

    def rank_listing(self, listing_id: int, search_id: int | None = None) -> DealRankResult:
        if self.session.get(ListingRecord, listing_id) is None:
            raise ValueError(f"Unknown listing: {listing_id}")
        warnings: list[str] = []
        if search_id is None:
            search_id = self.session.scalar(
                select(SearchListingMatchRecord.tracked_search_id)
                .where(SearchListingMatchRecord.listing_id == listing_id)
                .order_by(desc(SearchListingMatchRecord.last_seen_at))
            )
        deal = (
            DealScoringService(self.session).score_listing(listing_id, search_id)
            if search_id
            else None
        )
        deal_value = float(deal.score) if deal and deal.score is not None else None
        if deal_value is None:
            warnings.append("DealScore unavailable")

        market: MarketValueEstimate | None
        try:
            market = MarketValueService(self.session).estimate(listing_id)
        except ValueError:
            market = None
        market_value = self._market_score(market)
        if market_value is None:
            warnings.append("MarketValue unavailable or insufficient")

        assessment = ListingAIRepository(self.session).get_latest_assessment(listing_id)
        semantic = risk = None
        if assessment is not None:
            current_hash = build_analysis_input_hash(
                ListingAIRepository(self.session).build_context(listing_id),
                assessment.provider,
                assessment.model,
                assessment.prompt_version,
            )
            if current_hash != assessment.input_hash:
                warnings.append("AI assessment stale")
            else:
                semantic = float(assessment.semantic_score)
                risk = float(assessment.risk_score)
        if semantic is None:
            warnings.append("SemanticScore unavailable")

        raw = (
            ("deal", deal_value, self.config.deal_weight, 1.0),
            (
                "market",
                market_value,
                self.config.market_weight,
                market.confidence if market else 0.0,
            ),
            (
                "semantic",
                semantic,
                self.config.semantic_weight,
                float(assessment.deal_confidence)
                if assessment is not None and semantic is not None
                else 0.0,
            ),
        )
        total_weight = sum(weight for _, value, weight, _ in raw if value is not None)
        components: list[RankingComponent] = []
        base = 0.0
        confidence_parts: list[float] = []
        for name, value, weight, signal_confidence in raw:
            available = value is not None and total_weight > 0
            effective = weight / total_weight if available else 0.0
            confidence_value = float(signal_confidence or 0.0)
            adjusted = (
                value if value is None else value * confidence_value + 50.0 * (1 - confidence_value)
            )
            adjusted_value = float(adjusted) if adjusted is not None else 0.0
            contribution = effective * adjusted_value if available else 0.0
            base += contribution
            if available:
                confidence_parts.append(confidence_value)
            components.append(
                RankingComponent(
                    name,
                    value,
                    adjusted_value if available else None,
                    effective,
                    contribution,
                    available,
                    "available" if available else "missing",
                )
            )
        penalty = 0.0 if risk is None else self.config.max_risk_penalty * _clamp(risk) / 100.0
        if risk is None:
            warnings.append("RiskScore unavailable")
        confidence = max(
            0.0,
            min(
                1.0,
                (sum(confidence_parts) / len(confidence_parts) if confidence_parts else 0.0)
                * min(1.0, len(confidence_parts) / 3),
            ),
        )
        level = (
            RankingConfidence.INSUFFICIENT
            if confidence < self.config.insufficient_confidence
            else RankingConfidence.LOW
            if confidence < self.config.low_confidence
            else RankingConfidence.MEDIUM
            if confidence < self.config.medium_confidence
            else RankingConfidence.HIGH
        )
        return DealRankResult(
            listing_id,
            round(_clamp(base - penalty), 2),
            deal_value,
            market_value,
            semantic,
            round(penalty, 2),
            round(confidence, 3),
            level,
            tuple(components),
            tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _market_score(market: MarketValueEstimate | None) -> float | None:
        if market is None or market.confidence_level == MarketConfidence.INSUFFICIENT:
            return None
        discount = float(market.discount_vs_median or Decimal("0"))
        percentile = float(market.percentile if market.percentile is not None else 0.5)
        attractiveness = 50.0 + discount * 140.0 + (0.5 - percentile) * 45.0
        return round(_clamp(attractiveness), 2)
