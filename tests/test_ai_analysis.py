from decimal import Decimal

from wallapop_tracker.ai.models import ListingAnalysisContext
from wallapop_tracker.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_messages


def test_prompt_is_versioned_and_separates_system_from_payload() -> None:
    context = ListingAnalysisContext(listing_id="a", title="Sealed", price=Decimal("10"))
    messages = build_messages(context)
    assert PROMPT_VERSION == "listing-analysis-v1"
    assert messages[0]["role"] == "system"
    assert SYSTEM_PROMPT in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "market_median_price" in messages[1]["content"]
    assert "fair price" in SYSTEM_PROMPT.lower()


def test_prompt_preserves_empty_description_and_missing_seller_data() -> None:
    messages = build_messages(ListingAnalysisContext(listing_id="b", title="No details"))
    payload = messages[1]["content"]
    assert '"description": ""' in payload
    assert '"seller_rating": null' in payload
    assert '"discount_vs_market_median": null' in payload


def test_prompt_includes_supplied_market_metric_without_inventing_one() -> None:
    context = ListingAnalysisContext(
        listing_id="c",
        price=Decimal("650"),
        market_median_price=Decimal("800"),
        market_sample_size=30,
    )
    payload = build_messages(context)[1]["content"]
    assert '"discount_vs_market_median": "0.1875"' in payload
    assert "market_sample_size" in payload
