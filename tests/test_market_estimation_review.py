import json
from types import SimpleNamespace

from wallapop_tracker.reporting.market import (
    _comparable_score,
    _hard_incompatible,
    _norm,
    _token_similarity,
)


def snapshot(**values):
    defaults = {
        "category_id": "phones",
        "brand": "Apple",
        "title": "Apple iPhone 15 Pro 128 GB",
        "description": None,
        "condition": None,
        "attributes_json": json.dumps({"model": "iPhone 15 Pro", "capacity": "128 GB"}),
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_text_normalization_handles_spacing_and_generation_words():
    assert _norm("AirPods   Pro") == "airpods pro"
    assert _token_similarity("AirPods Pro 2 USB-C", "Airpods Pro Gen 2 USB C") > 0


def test_important_variant_attributes_change_comparable_score():
    target = snapshot()
    different_capacity = snapshot(
        title="Apple iPhone 15 Pro 256 GB",
        attributes_json=json.dumps({"model": "iPhone 15 Pro", "capacity": "256 GB"}),
    )
    different_model = snapshot(
        title="Apple iPhone 15 128 GB",
        attributes_json=json.dumps({"model": "iPhone 15", "capacity": "128 GB"}),
    )
    assert _hard_incompatible(target, different_capacity)
    assert _hard_incompatible(target, different_model)
    assert not _hard_incompatible(
        target, snapshot(attributes_json=json.dumps({"model": "iPhone 15 Pro"}))
    )
    assert _comparable_score(
        target,
        different_capacity,
    ) < _comparable_score(target, snapshot())
    assert _comparable_score(
        target,
        snapshot(
            title="Apple iPhone 15 128 GB",
            attributes_json=json.dumps({"model": "iPhone 15", "capacity": "128 GB"}),
        ),
    ) < _comparable_score(target, snapshot())


def test_condition_is_optional_matching_signal():
    assert _comparable_score(snapshot(condition="new"), snapshot(condition=None)) >= 0
