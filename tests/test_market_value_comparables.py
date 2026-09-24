from decimal import Decimal

from wallapop_tracker.services.market_value import _percentile, _quantile


def test_linear_quantiles_are_documented_convention():
    values = [Decimal(value) for value in [100, 110, 120, 130, 140]]
    assert _quantile(values, Decimal("0.25")) == Decimal("110")
    assert _quantile(values, Decimal("0.75")) == Decimal("130")


def test_percentile_ties_count_only_strictly_lower():
    values = [Decimal("100"), Decimal("100"), Decimal("120")]
    assert _percentile(Decimal("100"), values) == 0
    assert _percentile(Decimal("120"), values) == 2 / 3
