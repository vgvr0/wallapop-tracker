import json
from pathlib import Path
from typing import Any

from wallapop_tracker.parsers.items import parse_items_page
from wallapop_tracker.parsers.profile import parse_profile
from wallapop_tracker.parsers.reviews import parse_review_summary
from wallapop_tracker.parsers.stats import parse_profile_stats

RAW = Path(__file__).parent / "fixtures" / "raw" / "2026-09"


def load(name: str) -> Any:
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def test_raw_2026_09_profile_contract():
    profile = parse_profile(load("profile.json"))
    assert profile.user_id
    assert profile.slug


def test_raw_2026_09_stats_contract():
    stats = parse_profile_stats(load("stats.json"))
    assert stats.review_count is None or stats.review_count >= 0
    assert stats.published_count is None or stats.published_count >= 0


def test_raw_2026_09_reviews_contract():
    reviews = parse_review_summary(load("reviews_summary.json"))
    assert reviews.review_count is None or reviews.review_count >= 0
    assert reviews.rating_distribution is not None


def test_raw_2026_09_items_contract():
    page_1 = parse_items_page(load("items_page_1.json"), user_id="v4z4nyeyq8jy")
    page_2 = parse_items_page(load("items_page_2.json"), user_id="v4z4nyeyq8jy")
    assert page_1.items
    assert page_1.next_since
    assert page_2.next_since is None
    assert all(item.item_id for item in [*page_1.items, *page_2.items])
    assert all(item.price is None or item.price >= 0 for item in [*page_1.items, *page_2.items])
