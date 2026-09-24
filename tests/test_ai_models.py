from decimal import Decimal

import pytest
from pydantic import ValidationError

from wallapop_tracker.ai.models import ListingAIAnalysis, ListingAnalysisContext


def valid_analysis() -> dict[str, object]:
    return {
        "condition_assessment": "good",
        "condition_confidence": 0.8,
        "defects": [],
        "risk_flags": [],
        "positive_signals": ["clear description"],
        "missing_information": [],
        "semantic_score": 75,
        "risk_score": 10,
        "deal_quality": "good",
        "deal_confidence": 0.7,
        "summary": "The supplied description supports a generally good condition.",
    }


def test_context_distinguishes_missing_from_zero_and_calculates_discount() -> None:
    context = ListingAnalysisContext(
        listing_id="1", title="Item", price=Decimal("80"), market_median_price=Decimal("100")
    )
    assert context.seller_sales is None
    assert context.discount_vs_market_median == Decimal("0.2")


def test_analysis_is_structured_and_valid() -> None:
    assert ListingAIAnalysis.model_validate(valid_analysis()).semantic_score == 75


@pytest.mark.parametrize("field,value", [("semantic_score", 101), ("condition_confidence", 1.1)])
def test_analysis_rejects_out_of_range_values(field: str, value: float) -> None:
    payload = valid_analysis()
    payload[field] = value
    with pytest.raises(ValidationError):
        ListingAIAnalysis.model_validate(payload)


def test_analysis_rejects_unknown_enum() -> None:
    payload = valid_analysis()
    payload["condition_assessment"] = "excellent"
    with pytest.raises(ValidationError):
        ListingAIAnalysis.model_validate(payload)
