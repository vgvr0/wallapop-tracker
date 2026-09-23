from decimal import Decimal

import pytest

from wallapop_tracker.domain.filters import (
    ExcludeTextFilter,
    FieldExcludeFilter,
    FieldIncludeFilter,
    FilterEngine,
    IncludeMode,
    IncludeTextFilter,
    PriceFilter,
    RegexFilter,
    TextField,
    TitleFirstWordFilter,
    filters_from_config,
    normalize_first_word,
    normalize_text,
)
from wallapop_tracker.models import Listing


def listing(
    title: str | None = "iPhone 15 Pro 256GB",
    description: str | None = "Como nuevo · envío incluido",
    price: str | None = "600",
) -> Listing:
    return Listing(
        item_id="item-1",
        user_id="seller-1",
        title=title,
        description=description,
        price=Decimal(price) if price is not None else None,
    )


def test_price_filter_inclusive_bounds_and_missing_price():
    assert PriceFilter(Decimal("600"), Decimal("700")).matches(listing())
    assert not PriceFilter(max_price=Decimal("599")).matches(listing())
    assert not PriceFilter(min_price=Decimal("1")).matches(listing(price=None))


def test_include_any_and_all_are_case_insensitive():
    value = listing(title="iPhone 15 PRO 256GB")
    assert IncludeTextFilter(["pro", "android"], IncludeMode.ANY).matches(value)
    assert not IncludeTextFilter(["pro", "android"], IncludeMode.ALL).matches(value)
    assert IncludeTextFilter(["PRO", "256gb"], IncludeMode.ALL).matches(value)


def test_exclude_and_regex_handle_missing_fields_and_unicode():
    assert not ExcludeTextFilter(["ENVÍO"]).matches(listing())
    assert ExcludeTextFilter(["roto", "piezas"]).matches(listing())
    assert RegexFilter(r"256\s*gb", "title").matches(listing())
    assert RegexFilter("envío", "description").matches(listing())
    assert not RegexFilter("pro", "description").matches(listing())
    assert RegexFilter("anything", "both").matches(listing(title=None, description="anything"))


def test_invalid_regex_and_composition_are_controlled():
    with pytest.raises(ValueError, match="invalid regex"):
        RegexFilter("[")
    engine = FilterEngine([PriceFilter(max_price=Decimal("650")), ExcludeTextFilter(["roto"])])
    assert engine.matches(listing())
    assert not engine.matches(listing(title="roto iPhone"))


def test_normalization_is_shared_and_never_mutates_text():
    assert normalize_text("  iPhone   15  PRO ") == "iphone 15 pro"
    assert normalize_text(None) == ""
    assert normalize_first_word("  ¡iPhone 15!  ") == "iphone"
    assert normalize_first_word("iPhone-15 Pro") == "iphone-15"
    assert normalize_first_word("   ") == ""
    assert normalize_first_word(None) == ""
    value = listing(title="  iPhone   15  ")
    IncludeTextFilter(["iphone"]).matches(value)
    assert value.title == "  iPhone   15  "


def test_title_and_description_include_are_independent_and_configurable():
    value = listing(title="iPhone 15 Pro", description="Con factura y caja original")
    title_any = FieldIncludeFilter(["iphone", "android"], TextField.TITLE, IncludeMode.ANY)
    title_all = FieldIncludeFilter(["iphone", "android"], TextField.TITLE, IncludeMode.ALL)
    assert title_any.matches(value)
    assert not title_all.matches(value)
    assert FieldIncludeFilter(["IPHONE", "15 pro"], TextField.TITLE, "all").matches(value)

    description_any = FieldIncludeFilter(["factura", "garantia"], TextField.DESCRIPTION)
    description_all = FieldIncludeFilter(
        ["factura", "garantia"], TextField.DESCRIPTION, IncludeMode.ALL
    )
    assert description_any.matches(value)
    assert not description_all.matches(value)
    # A term that only exists in the description never satisfies a title filter.
    assert not FieldIncludeFilter(["factura"], TextField.TITLE).matches(value)
    assert not FieldIncludeFilter(["iphone"], TextField.DESCRIPTION).matches(value)


def test_title_and_description_exclude_reject_on_any_term():
    value = listing(title="Funda iPhone 15 Pro", description="Ideal para piezas")
    assert not FieldExcludeFilter(["funda"], TextField.TITLE).matches(value)
    assert not FieldExcludeFilter(["FUNDA", "carcasa"], TextField.TITLE).matches(value)
    assert FieldExcludeFilter(["carcasa"], TextField.TITLE).matches(value)
    assert not FieldExcludeFilter(["para piezas"], TextField.DESCRIPTION).matches(value)
    assert FieldExcludeFilter(["para piezas"], TextField.TITLE).matches(value)


def test_first_word_filters_are_exact_case_insensitive_and_punctuation_aware():
    assert TitleFirstWordFilter(["iphone"]).matches(listing(title="iPhone 15 Pro"))
    assert TitleFirstWordFilter(["iphone"]).matches(listing(title="  iPhone 15 Pro"))
    assert TitleFirstWordFilter(["iphone"]).matches(listing(title="¡iPhone 15 Pro!"))
    assert TitleFirstWordFilter(["iphone"]).matches(listing(title="IPHONE"))
    # Exact match, never substring.
    assert not TitleFirstWordFilter(["iph"]).matches(listing(title="iPhone 15 Pro"))
    assert not TitleFirstWordFilter(["iphone"]).matches(listing(title="Funda iPhone"))
    assert not TitleFirstWordFilter(["iphone"]).matches(listing(title="iPhone-15 Pro"))
    assert TitleFirstWordFilter(["iphone-15"]).matches(listing(title="iPhone-15 Pro"))
    # A single-word title still matches on the same normalized word.
    assert TitleFirstWordFilter(["iphone"]).matches(listing(title="iPhone"))

    assert not TitleFirstWordFilter(["lote"], exclude=True).matches(listing(title="Lote iPhone"))
    assert TitleFirstWordFilter(["lote"], exclude=True).matches(listing(title="iPhone lote"))
    assert TitleFirstWordFilter(["lote"], exclude=True).matches(listing(title=None))


def test_first_word_filters_on_empty_or_missing_titles():
    empty_titles = (None, "", "   ", "-·-")
    for title in empty_titles:
        assert not TitleFirstWordFilter(["iphone"]).matches(listing(title=title))
        assert TitleFirstWordFilter(["iphone"], exclude=True).matches(listing(title=title))
    # An empty configuration is a no-op in both directions.
    assert TitleFirstWordFilter([]).matches(listing(title=None))
    assert TitleFirstWordFilter([], exclude=True).matches(listing(title=None))


def test_field_include_and_exclude_handle_missing_and_blank_fields():
    for title in (None, "", "   "):
        assert not FieldIncludeFilter(["iphone"], TextField.TITLE).matches(listing(title=title))
        assert FieldExcludeFilter(["iphone"], TextField.TITLE).matches(listing(title=title))
    for description in (None, "", "   "):
        assert not FieldIncludeFilter(["factura"], TextField.DESCRIPTION).matches(
            listing(description=description)
        )
        assert FieldExcludeFilter(["factura"], TextField.DESCRIPTION).matches(
            listing(description=description)
        )
    assert FieldIncludeFilter([], TextField.TITLE).matches(listing(title=None))
    assert FieldExcludeFilter([], TextField.DESCRIPTION).matches(listing(description=None))


def test_same_term_in_include_and_exclude_rejects_the_listing():
    engine = filters_from_config({"title_include": ["iphone"], "title_exclude": ["iphone"]})
    assert not engine.matches(listing(title="iPhone 15"))
    engine = filters_from_config(
        {"description_include": ["factura"], "description_exclude": ["factura"]}
    )
    assert not engine.matches(listing(description="Con factura"))


def test_filters_from_config_builds_the_documented_order():
    engine = filters_from_config(
        {
            "min_price": 100,
            "max_price": 700,
            "condition": "as_good_as_new",
            "category_id": "24200",
            "brand": ["apple"],
            "model": "iphone 15",
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
    assert [type(item).__name__ for item in engine.filters] == [
        "PriceFilter",
        "ConditionFilter",
        "CategoryFilter",
        "ValueFilter",
        "ValueFilter",
        "IncludeTextFilter",
        "ExcludeTextFilter",
        "FieldIncludeFilter",
        "FieldIncludeFilter",
        "FieldExcludeFilter",
        "FieldExcludeFilter",
        "TitleFirstWordFilter",
        "TitleFirstWordFilter",
        "RegexFilter",
    ]


def test_filters_from_config_accepts_aliases_and_ignores_empty_configuration():
    aliased = filters_from_config({"title_must_include": "iphone"})
    assert aliased.matches(listing(title="iPhone 15"))
    assert not aliased.matches(listing(title=None, description="iphone"))
    aliased_description = filters_from_config({"description_must_include": ["factura"]})
    assert aliased_description.matches(listing(description="CON FACTURA"))

    assert filters_from_config({}).matches(listing(title=None, description=None))
    empty = filters_from_config(
        {"title_include": [], "title_exclude": [], "title_first_word_include": []}
    )
    assert empty.matches(listing(title="Funda"))


def test_advanced_filters_combine_with_price_and_regex_semantics():
    config = {
        "min_price": "100",
        "max_price": "700",
        "title_include": ["iphone"],
        "description_include": ["factura"],
        "title_exclude": ["funda"],
        "description_exclude": ["para piezas"],
        "title_first_word_include": ["iphone"],
        "regex": r"256\s*gb",
        "regex_target": "title",
    }
    engine = filters_from_config(config)
    matching = listing(title="iPhone 15 Pro 256GB", description="Con factura y caja", price="600")
    assert engine.matches(matching)
    # Each condition alone is enough to reject the listing.
    assert not engine.matches(
        listing(title="iPhone 15 Pro 256GB", description="Sin papeles", price="600")
    )
    assert not engine.matches(
        listing(title="Funda iPhone 256GB", description="Con factura", price="600")
    )
    assert not engine.matches(
        listing(title="iPhone 15 256GB", description="Para piezas", price="600")
    )
    assert not engine.matches(
        listing(title="iPhone 15 Pro 256GB", description="Con factura", price="900")
    )
    assert not engine.matches(
        listing(title="iPhone 15 Pro 128GB", description="Con factura", price="600")
    )
    assert not engine.matches(
        listing(title="Lote iPhone 15 256GB", description="Con factura", price="600")
    )
