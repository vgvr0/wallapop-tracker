from wallapop_tracker.ai.hashing import build_analysis_input_hash
from wallapop_tracker.ai.models import ListingAnalysisContext


def test_same_input_is_stable_and_dict_order_is_irrelevant() -> None:
    context = ListingAnalysisContext(listing_id="1", title="A")
    first = build_analysis_input_hash(context, "provider", "model", "v1")
    second = build_analysis_input_hash({"title": "A", "listing_id": "1"}, "provider", "model", "v1")
    assert first == second


def test_relevant_inputs_change_hash() -> None:
    base = ListingAnalysisContext(listing_id="1", title="A")
    original = build_analysis_input_hash(base, "provider", "model", "v1")
    assert (
        build_analysis_input_hash(
            ListingAnalysisContext(listing_id="2", title="A"), "provider", "model", "v1"
        )
        != original
    )
    assert build_analysis_input_hash(base, "provider", "other-model", "v1") != original
    assert build_analysis_input_hash(base, "provider", "model", "v2") != original
