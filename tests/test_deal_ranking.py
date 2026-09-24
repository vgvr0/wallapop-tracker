from decimal import Decimal

import pytest
from prometheus_client import CollectorRegistry

from wallapop_tracker.domain.deal_ranking import RankingConfidence
from wallapop_tracker.observability import Metrics
from wallapop_tracker.services.deal_ranking import DealRankingService, RankingConfig
from wallapop_tracker.services.market_value import (
    MarketConfidence,
    MarketValueEstimate,
    SelectionSummary,
)


def estimate(confidence_level, confidence=0.9, discount=Decimal("-0.2"), percentile=0.1):
    return MarketValueEstimate(
        1,
        Decimal("80"),
        Decimal("100"),
        None,
        None,
        None,
        10,
        discount,
        percentile,
        confidence,
        confidence_level,
        (),
        SelectionSummary(10, 10, 0),
        (),
    )


def test_config_validation_and_non_unit_weights():
    assert RankingConfig(deal_weight=2, market_weight=1, semantic_weight=0).deal_weight == 2
    with pytest.raises(ValueError):
        RankingConfig(deal_weight=-1)
    with pytest.raises(ValueError):
        RankingConfig(deal_weight=0, market_weight=0, semantic_weight=0)
    with pytest.raises(ValueError):
        RankingConfig(max_risk_penalty=-1)


def test_market_confidence_policy():
    high = DealRankingService._market_score(estimate(MarketConfidence.HIGH))
    low = DealRankingService._market_score(estimate(MarketConfidence.LOW, confidence=0.3))
    insufficient = DealRankingService._market_score(estimate(MarketConfidence.INSUFFICIENT))
    assert high is not None and low is not None
    assert insufficient is None


def test_result_contract_bounds_and_confidence_labels():
    assert list(RankingConfidence) == [
        RankingConfidence.INSUFFICIENT,
        RankingConfidence.LOW,
        RankingConfidence.MEDIUM,
        RankingConfidence.HIGH,
    ]


def test_ranking_metrics_are_low_cardinality_and_injectable():
    metrics = Metrics(CollectorRegistry())
    metrics.wallapop_deal_ranking_requests_total.inc()
    metrics.wallapop_deal_ranking_failures_total.inc()
    metrics.wallapop_deal_ranking_duration_seconds.observe(0.01)
    assert metrics.wallapop_deal_ranking_requests_total._value.get() == 1
    assert metrics.wallapop_deal_ranking_failures_total._value.get() == 1
