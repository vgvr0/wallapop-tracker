"""Coverage for the filter explanation API (``FilterEngine.evaluate``)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from wallapop_tracker.domain.filters import (
    FilterEngine,
    FilterEvaluation,
    FilterTrace,
    filters_from_config,
)
from wallapop_tracker.models import Listing
from wallapop_tracker.services.filter_explanation import (
    evaluation_warnings,
    explain_search_listing,
    search_filter_config,
)
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    SnapshotRepository,
    TrackedSearchRepository,
    TrackingRunRepository,
)


def listing(
    title: str | None = "iPhone 15 Pro 256GB",
    description: str | None = "Con factura y caja",
    price: str | None = "600",
    **overrides: object,
) -> Listing:
    values: dict[str, object] = {
        "item_id": "item-1",
        "user_id": "seller-1",
        "title": title,
        "description": description,
        "price": Decimal(price) if price is not None else None,
    }
    values.update(overrides)
    return Listing(**values)


class _AlwaysRejecting:
    """Custom filter that only implements the ``ListingFilter`` contract."""

    def matches(self, listing: Listing) -> bool:
        return False


def test_engine_without_filters_matches_and_emits_no_trace():
    evaluation = FilterEngine().evaluate(listing())

    assert evaluation.matched is True
    assert evaluation.complete is True
    assert evaluation.traces == ()
    assert filters_from_config({}).evaluate(listing()).traces == ()


def test_filter_trace_accepts_the_unknown_state():
    trace = FilterTrace(
        filter_name="model",
        passed=None,
        expected_value=("iphone 15",),
        reason="model is not persisted in listing snapshots",
    )

    assert trace.passed is None
    assert trace.matched_values == ()


@pytest.mark.parametrize(
    ("states", "matched", "complete"),
    [
        ((True, True), True, True),
        ((True, False), False, True),
        ((False, True), False, True),
        ((True, True, True), True, True),
        ((True, None), None, False),
        ((None, True), None, False),
        ((False, None), False, False),
        ((None, False), False, False),
        ((None, None), None, False),
        ((), True, True),
    ],
)
def test_evaluation_aggregation_is_tri_state(states, matched, complete):
    traces = tuple(
        FilterTrace(filter_name=f"filter-{index}", passed=state)
        for index, state in enumerate(states)
    )

    evaluation = FilterEvaluation.from_traces(traces)

    assert evaluation.matched is matched
    assert evaluation.complete is complete
    assert evaluation.traces == traces


def test_price_traces_cover_each_configured_bound():
    passing = filters_from_config({"max_price": 500}).evaluate(listing(price="450"))
    assert passing.matched is True
    assert passing.traces == (
        FilterTrace(
            filter_name="max_price",
            passed=True,
            actual_value=Decimal("450"),
            expected_value=Decimal("500"),
        ),
    )

    bounded = filters_from_config({"min_price": 100, "max_price": 500})
    evaluation = bounded.evaluate(listing(price="450"))
    assert [trace.filter_name for trace in evaluation.traces] == ["min_price", "max_price"]
    assert [trace.expected_value for trace in evaluation.traces] == [
        Decimal("100"),
        Decimal("500"),
    ]
    assert evaluation.matched is True

    rejected = bounded.evaluate(listing(price="790"))
    assert rejected.matched is False
    assert [trace.passed for trace in rejected.traces] == [True, False]


def test_missing_price_is_explained_without_inventing_a_value():
    evaluation = filters_from_config({"min_price": 100, "max_price": 500}).evaluate(
        listing(price=None)
    )

    assert evaluation.matched is False
    assert [trace.actual_value for trace in evaluation.traces] == [None, None]
    assert [trace.reason for trace in evaluation.traces] == ["price missing", "price missing"]


def test_unconfigured_filters_do_not_appear_in_the_trace():
    engine = filters_from_config(
        {
            "include": [],
            "exclude": [],
            "condition": [],
            "brand": [],
            "title_include": [],
            "description_include": [],
            "title_exclude": [],
            "description_exclude": [],
            "title_first_word_include": [],
            "title_first_word_exclude": [],
        }
    )

    evaluation = engine.evaluate(listing())

    assert evaluation.traces == ()
    assert evaluation.matched is True


def test_every_filter_is_evaluated_even_after_an_intermediate_failure():
    engine = filters_from_config(
        {
            "max_price": 500,
            "title_include": ["rtx 4070"],
            "description_include": ["garantia"],
            "title_exclude": ["repuesto"],
            "regex": "gtx",
        }
    )

    evaluation = engine.evaluate(
        listing(title="RTX 4070 repuesto", description="Sin papeles", price="450")
    )

    assert [trace.filter_name for trace in evaluation.traces] == [
        "max_price",
        "title_include",
        "description_include",
        "title_exclude",
        "regex",
    ]
    assert [trace.passed for trace in evaluation.traces] == [True, True, False, False, False]
    assert evaluation.matched is False
    assert evaluation.matched == all(trace.passed for trace in evaluation.traces)
    # The failing filters expose the values that produced the verdict.
    description = evaluation.traces[2]
    assert description.expected_value == ("garantia",)
    assert description.matched_values == ()
    assert evaluation.traces[3].matched_values == ("repuesto",)


def test_trace_order_follows_the_documented_evaluation_order():
    engine = filters_from_config(
        {
            "min_price": 100,
            "max_price": 700,
            "condition": "as_good_as_new",
            "category_id": "24200",
            "brand": ["apple"],
            "model": "iphone 15",
            "latitude": 41.39,
            "longitude": 2.16,
            "max_distance_km": 25,
            "include": ["256gb"],
            "exclude": ["roto"],
            "title_include": ["iphone"],
            "description_include": ["factura"],
            "title_exclude": ["funda"],
            "description_exclude": ["para piezas"],
            "title_first_word_include": ["iphone"],
            "title_first_word_exclude": ["lote"],
            "regex": r"256\s*gb",
            "regex_target": "title",
        }
    )

    evaluation = engine.evaluate(
        listing(
            condition_code="as_good_as_new",
            category_id="24200",
            brand="apple",
            model="iphone 15",
            latitude=41.39,
            longitude=2.16,
        )
    )

    assert [trace.filter_name for trace in evaluation.traces] == [
        "min_price",
        "max_price",
        "condition",
        "category_id",
        "brand",
        "model",
        "distance",
        "include",
        "exclude",
        "title_include",
        "description_include",
        "title_exclude",
        "description_exclude",
        "title_first_word_include",
        "title_first_word_exclude",
        "regex",
    ]
    assert evaluation.matched is True
    assert all(trace.passed for trace in evaluation.traces)


def test_include_traces_report_the_matched_terms():
    any_mode = filters_from_config({"include": ["256gb", "roto"]}).evaluate(listing())
    trace = any_mode.traces[0]
    assert trace.filter_name == "include"
    assert trace.passed is True
    assert trace.expected_value == ("256gb", "roto")
    assert trace.matched_values == ("256gb",)

    all_mode = filters_from_config({"include": ["iphone", "roto"], "include_mode": "all"}).evaluate(
        listing()
    )
    trace = all_mode.traces[0]
    assert trace.passed is False
    assert trace.expected_value == ("iphone", "roto")
    assert trace.matched_values == ("iphone",)


def test_exclude_trace_reports_the_rejecting_terms():
    evaluation = filters_from_config({"exclude": ["roto", "piezas"]}).evaluate(
        listing(title="iPhone 15 Pro", description="Ideal para piezas")
    )

    trace = evaluation.traces[0]
    assert trace.filter_name == "exclude"
    assert trace.passed is False
    assert trace.expected_value == ("roto", "piezas")
    assert trace.matched_values == ("piezas",)
    assert evaluation.matched is False


def test_field_traces_are_independent_per_field_and_mode():
    engine = filters_from_config(
        {
            "title_include": ["iphone", "15 pro"],
            "title_include_mode": "all",
            "description_include": ["garantia"],
            "description_include_mode": "all",
            "title_exclude": ["funda"],
            "description_exclude": ["piezas"],
        }
    )

    traces = {
        trace.filter_name: trace
        for trace in engine.evaluate(
            listing(title="iPhone 15 Pro", description="Ideal para piezas")
        ).traces
    }

    assert traces["title_include"].passed is True
    assert traces["title_include"].expected_value == ("iphone", "15 pro")
    assert traces["title_include"].matched_values == ("iphone", "15 pro")
    assert traces["description_include"].passed is False
    assert traces["description_include"].expected_value == ("garantia",)
    assert traces["description_include"].matched_values == ()
    assert traces["title_exclude"].passed is True
    assert traces["title_exclude"].matched_values == ()
    assert traces["description_exclude"].passed is False
    assert traces["description_exclude"].matched_values == ("piezas",)


def test_first_word_traces_are_exact_and_punctuation_aware():
    included = filters_from_config({"title_first_word_include": ["asus", "msi"]}).evaluate(
        listing(title="  ¡ASUS ROG!")
    )
    trace = included.traces[0]
    assert trace.filter_name == "title_first_word_include"
    assert trace.actual_value == "asus"
    assert trace.expected_value == ("asus", "msi")
    assert trace.matched_values == ("asus",)
    assert trace.passed is True

    # Exact match, never substring.
    substring = filters_from_config({"title_first_word_include": ["asu"]}).evaluate(
        listing(title="Asus ROG")
    )
    assert substring.traces[0].passed is False
    assert substring.traces[0].matched_values == ()

    excluded = filters_from_config({"title_first_word_exclude": ["lote"]}).evaluate(
        listing(title="Lote iPhone")
    )
    assert excluded.traces[0].filter_name == "title_first_word_exclude"
    assert excluded.traces[0].actual_value == "lote"
    assert excluded.traces[0].matched_values == ("lote",)
    assert excluded.traces[0].passed is False

    missing_title = filters_from_config({"title_first_word_include": ["iphone"]}).evaluate(
        listing(title=None)
    )
    assert missing_title.traces[0].actual_value == ""
    assert missing_title.traces[0].passed is False


def test_regex_trace_reports_the_target_pattern_and_match():
    matching = filters_from_config({"regex": r"256\s*gb", "regex_target": "title"}).evaluate(
        listing(title="iPhone 256 GB")
    )
    assert matching.traces == (
        FilterTrace(
            filter_name="regex",
            passed=True,
            actual_value="title",
            expected_value=r"256\s*gb",
            matched_values=("256 GB",),
        ),
    )

    wrong_field = filters_from_config(
        {"regex": r"256\s*gb", "regex_target": "description"}
    ).evaluate(listing(title="iPhone 256 GB"))
    assert wrong_field.traces[0].passed is False
    assert wrong_field.traces[0].actual_value == "description"
    assert wrong_field.traces[0].matched_values == ()

    both = filters_from_config({"regex": "factura"}).evaluate(listing())
    assert both.traces[0].passed is True
    assert both.traces[0].actual_value == "both"


def test_structured_traces_report_actual_and_expected_values():
    engine = filters_from_config(
        {
            "category_id": "24200",
            "brand": ["Apple"],
            "condition": "as_good_as_new",
            "latitude": 41.39,
            "longitude": 2.16,
            "max_distance_km": 30,
        }
    )

    evaluation = engine.evaluate(
        listing(
            category_id="24200",
            brand="APPLE",
            condition_code="as_good_as_new",
            latitude=41.4,
            longitude=2.17,
        )
    )
    traces = {trace.filter_name: trace for trace in evaluation.traces}

    assert traces["category_id"].actual_value == "24200"
    assert traces["category_id"].expected_value == "24200"
    assert traces["brand"].actual_value == "APPLE"
    assert traces["brand"].expected_value == ("apple",)
    assert traces["brand"].passed is True
    assert traces["condition"].actual_value == "as_good_as_new"
    assert traces["condition"].expected_value == ("as_good_as_new",)
    assert traces["distance"].expected_value == 30
    assert isinstance(traces["distance"].actual_value, float)
    assert 0 < traces["distance"].actual_value < 30
    assert traces["distance"].passed is True
    assert evaluation.matched is True

    rejected = engine.evaluate(listing(category_id="999", brand="Samsung", condition_code=None))
    rejected_traces = {trace.filter_name: trace for trace in rejected.traces}
    assert rejected_traces["category_id"].passed is False
    assert rejected_traces["brand"].passed is False
    assert rejected_traces["condition"].passed is False
    assert rejected_traces["condition"].actual_value is None
    assert rejected_traces["condition"].reason == "condition missing"


def test_distance_trace_reports_the_missing_coordinates():
    engine = filters_from_config({"latitude": 41.39, "longitude": 2.16, "max_distance_km": 25})

    evaluation = engine.evaluate(listing(latitude=None, longitude=None))

    trace = evaluation.traces[0]
    assert trace.filter_name == "distance"
    assert trace.passed is False
    assert trace.actual_value is None
    assert trace.expected_value == 25
    assert trace.reason == "listing coordinates missing"
    # A real listing without coordinates is still rejected by the tracker; only
    # the storage-backed explanation knows that those coordinates were never
    # persisted and must report UNKNOWN instead.
    assert evaluation.matched is False
    assert evaluation.complete is True


def test_normalization_is_visible_in_the_trace():
    normalized = filters_from_config({"title_include": ["  IPHONE   15 "]}).evaluate(
        listing(title="  iPhone   15  Pro ")
    )
    trace = normalized.traces[0]
    assert trace.expected_value == ("iphone 15",)
    assert trace.matched_values == ("iphone 15",)
    assert trace.passed is True

    for value in (listing(title=None), listing(title=""), listing(title="   ")):
        evaluation = filters_from_config({"title_include": ["iphone"]}).evaluate(value)
        assert evaluation.traces[0].passed is False
        assert evaluation.traces[0].matched_values == ()

    description = filters_from_config({"description_include": ["factura"]}).evaluate(
        listing(description=None)
    )
    assert description.traces[0].passed is False
    assert description.traces[0].matched_values == ()


def test_custom_filters_without_explain_are_traced_generically():
    engine = FilterEngine([_AlwaysRejecting()])

    evaluation = engine.evaluate(listing())

    assert evaluation.matched is False
    assert evaluation.traces == (FilterTrace(filter_name="_AlwaysRejecting", passed=False),)
    assert engine.matches(listing()) is False


def test_engine_keeps_two_valued_results_for_real_listings():
    engine = filters_from_config(
        {
            "min_price": 100,
            "max_price": 700,
            "title_include": ["iphone"],
            "models": ["iphone 15"],
            "latitude": 41.39,
            "longitude": 2.16,
            "max_distance_km": 25,
        }
    )
    real_listings = (
        listing(),
        listing(price=None),
        listing(title=None, description=None),
        listing(model="iphone 15", latitude=41.39, longitude=2.16),
    )

    for value in real_listings:
        evaluation = engine.evaluate(value)
        assert all(trace.passed is not None for trace in evaluation.traces)
        assert evaluation.complete is True
        assert isinstance(evaluation.matched, bool)
        assert engine.matches(value) is evaluation.matched


_CONFIGS: tuple[dict[str, object], ...] = (
    {},
    {"max_price": 500},
    {"min_price": 100, "max_price": 500},
    {"include": ["iphone"], "include_mode": "all"},
    {"exclude": ["roto"]},
    {"title_include": ["iphone"], "title_include_mode": "all"},
    {"description_include": ["factura"]},
    {"title_exclude": ["funda"]},
    {"description_exclude": ["piezas"]},
    {"title_first_word_include": ["iphone"]},
    {"title_first_word_exclude": ["lote"]},
    {"regex": r"256\s*gb"},
    {"condition": "as_good_as_new", "category_id": "24200", "brand": ["apple"]},
    {"models": ["iphone 15"], "latitude": 41.39, "longitude": 2.16, "max_distance_km": 25},
)

_LISTINGS: tuple[Listing, ...] = (
    listing(),
    listing(price=None),
    listing(title=None, description=None),
    listing(title="Funda iPhone rota", description="Para piezas", price="900"),
    listing(title="iPhone 15 Pro 256GB", price="450", condition_code="as_good_as_new"),
    listing(
        model="iphone 15",
        brand="apple",
        latitude=41.39,
        longitude=2.16,
        category_id="24200",
    ),
)


@pytest.mark.parametrize("config", _CONFIGS)
def test_matches_and_evaluate_agree_for_representative_configs(config):
    engine = filters_from_config(config)

    for value in _LISTINGS:
        evaluation = engine.evaluate(value)
        assert evaluation.complete is True
        assert all(trace.passed is not None for trace in evaluation.traces)
        assert engine.matches(value) is evaluation.matched
        assert evaluation.matched == FilterEvaluation.from_traces(evaluation.traces).matched


def _store_listing(session, value: Listing) -> tuple[int, int]:
    search_id = (
        TrackedSearchRepository(session)
        .create(
            "iphone",
            name="iphone limpio",
            max_price=Decimal("700"),
            filters={
                "title_include": ["iphone"],
                "description_include": ["garantia"],
                "title_exclude": ["funda"],
            },
        )
        .id
    )
    run = TrackingRunRepository(session).start_search_run(search_id)
    TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
    record, _ = ListingRepository(session).get_or_create_global_listing(
        value, None, observed_at=datetime.now(UTC), tracking_run_id=run.id
    )
    SnapshotRepository(session).save_listing_snapshot(record.id, run.id, value)
    return search_id, record.id


def test_explain_search_listing_evaluates_the_stored_snapshot(database):
    matching = listing(title="iPhone 15 Pro", description="Con factura y garantia", price="450")
    rejected = listing(item_id="item-2", title="iPhone 15 Pro", description="Con factura")

    with database.transaction() as session:
        search_id, matching_id = _store_listing(session, matching)
        run = TrackingRunRepository(session).start_search_run(search_id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            rejected, None, observed_at=datetime.now(UTC), tracking_run_id=run.id
        )
        SnapshotRepository(session).save_listing_snapshot(record.id, run.id, rejected)
        rejected_id = record.id

    with database.session() as session:
        matched = explain_search_listing(session, search_id, matching_id)
        failing = explain_search_listing(session, search_id, rejected_id)

    assert matched.matched is True
    assert matched.complete is True
    assert [trace.filter_name for trace in matched.evaluation.traces] == [
        "max_price",
        "title_include",
        "description_include",
        "title_exclude",
    ]
    assert matched.warnings == ()

    # A rejected listing does not need to be a stored match of the search.
    assert failing.matched is False
    assert [(trace.filter_name, trace.passed) for trace in failing.evaluation.traces] == [
        ("max_price", True),
        ("title_include", True),
        ("description_include", False),
        ("title_exclude", True),
    ]
    assert failing.evaluation.traces[2].expected_value == ("garantia",)
    assert failing.evaluation.traces[2].matched_values == ()


def test_explain_uses_the_price_columns_of_the_search(database):
    value = listing(price="650")

    with database.transaction() as session:
        search_id = (
            TrackedSearchRepository(session)
            .create("iphone", max_price=Decimal("700"), filters={"max_price": 100})
            .id
        )
        run = TrackingRunRepository(session).start_search_run(search_id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        SnapshotRepository(session).save_listing_snapshot(record.id, run.id, value)
        listing_id = record.id

    with database.session() as session:
        search = TrackedSearchRepository(session).get(search_id)
        assert search is not None
        assert search_filter_config(search)["max_price"] == "700.00"
        explanation = explain_search_listing(session, search_id, listing_id)

    trace = explanation.evaluation.traces[0]
    assert trace.filter_name == "max_price"
    assert trace.expected_value == Decimal("700.00")
    assert trace.passed is True


def test_explain_marks_filters_without_persisted_inputs_as_unknown(database):
    value = listing(brand="APPLE")
    filters = {
        "brand": ["apple"],
        "models": ["iphone 15"],
        "latitude": 41.39,
        "longitude": 2.16,
        "max_distance_km": 25,
    }

    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("iphone", filters=filters).id
        run = TrackingRunRepository(session).start_search_run(search_id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        SnapshotRepository(session).save_listing_snapshot(record.id, run.id, value)
        listing_id = record.id

    with database.session() as session:
        explanation = explain_search_listing(session, search_id, listing_id)

    assert [trace.filter_name for trace in explanation.evaluation.traces] == [
        "brand",
        "model",
        "distance",
    ]
    assert [trace.passed for trace in explanation.evaluation.traces] == [True, None, None]
    # A known PASS next to UNKNOWN conditions cannot claim a match.
    assert explanation.matched is None
    assert explanation.complete is False
    assert explanation.warnings == (
        "model could not be evaluated from persisted data",
        "distance could not be evaluated from persisted data",
    )

    brand, model, distance = explanation.evaluation.traces
    assert brand.passed is True
    assert brand.expected_value == ("apple",)
    assert model == FilterTrace(
        filter_name="model",
        passed=None,
        expected_value=("iphone 15",),
        reason="model is not persisted in listing snapshots",
    )
    assert distance.passed is None
    assert distance.actual_value is None
    assert distance.expected_value == 25
    assert distance.reason == "distance cannot be evaluated because coordinates are not persisted"


def test_explain_keeps_a_known_failure_next_to_unknown_inputs(database):
    value = listing(title="Funda iPhone", description="Sin papeles")
    filters = {"title_include": ["garantia"], "models": ["iphone 15"]}

    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("iphone", filters=filters).id
        run = TrackingRunRepository(session).start_search_run(search_id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        SnapshotRepository(session).save_listing_snapshot(record.id, run.id, value)
        listing_id = record.id

    with database.session() as session:
        explanation = explain_search_listing(session, search_id, listing_id)

    # FAIL wins over UNKNOWN, but the evaluation is still incomplete.
    assert [(trace.filter_name, trace.passed) for trace in explanation.evaluation.traces] == [
        ("model", None),
        ("title_include", False),
    ]
    assert explanation.matched is False
    assert explanation.complete is False


def test_evaluation_warnings_summarize_the_unknown_conditions():
    assert evaluation_warnings(
        FilterEvaluation.from_traces((FilterTrace(filter_name="model", passed=None),))
    ) == ("model could not be evaluated from persisted data",)
    assert (
        evaluation_warnings(
            FilterEvaluation.from_traces((FilterTrace(filter_name="max_price", passed=True),))
        )
        == ()
    )


def test_explain_tolerates_a_listing_without_snapshots(database):
    value = listing()

    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("iphone").id
        run = TrackingRunRepository(session).start_search_run(search_id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        listing_id = record.id

    with database.session() as session:
        explanation = explain_search_listing(session, search_id, listing_id)

    assert explanation.matched is True
    assert explanation.complete is True
    assert explanation.evaluation.traces == ()


def test_explain_rejects_unknown_search_or_listing(database):
    value = listing()

    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("iphone").id
        run = TrackingRunRepository(session).start_search_run(search_id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        listing_id = record.id

    with database.session() as session:
        with pytest.raises(ValueError, match="Unknown search"):
            explain_search_listing(session, 999, listing_id)
        with pytest.raises(ValueError, match="Unknown listing"):
            explain_search_listing(session, search_id, 999)
