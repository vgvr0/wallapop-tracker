from decimal import Decimal

import pytest

from wallapop_tracker.domain.filters import (
    ExcludeTextFilter,
    FilterEngine,
    IncludeMode,
    IncludeTextFilter,
    PriceFilter,
    RegexFilter,
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
