from datetime import UTC
from decimal import Decimal

from wallapop_tracker.parsers.items import parse_items_page
from wallapop_tracker.parsers.profile import parse_profile
from wallapop_tracker.parsers.reviews import parse_review_summary
from wallapop_tracker.parsers.stats import parse_profile_stats


def test_parse_profile(fixture_data):
    profile = parse_profile(fixture_data("profile.json"))
    assert profile.user_id == "user-1"
    assert profile.name == "Ana"
    assert profile.location == "Madrid, 28001"
    assert profile.registered_at.tzinfo == UTC


def test_parse_stats(fixture_data):
    stats = parse_profile_stats(fixture_data("stats.json"))
    assert stats.rating == 4.5
    assert stats.review_count == 12
    assert stats.published_count == 3
    assert stats.sold_count == 7


def test_parse_review_summary(fixture_data):
    summary = parse_review_summary(fixture_data("reviews_summary.json"))
    assert summary.review_count == 12
    assert summary.rating_distribution == {1: 2, 2: 3, 3: 5, 4: 10, 5: 80}


def test_parse_items_page_and_price_dates(fixture_data):
    page = parse_items_page(fixture_data("items_page_1.json"), user_id="user-1")
    assert page.next_since == "cursor-1"
    assert page.items[0].price == Decimal("22")
    assert page.items[0].created_at is None
    assert page.items[0].url == "https://www.wallapop.com/item/camara-1"


def test_parse_second_item(fixture_data):
    item = parse_items_page(fixture_data("items_page_2.json"), user_id="user-1").items[0]
    assert item.price == Decimal("90.50")
    assert item.reserved is True
    assert item.modified_at is None


def test_parse_explicit_item_dates_and_decimal_amount():
    page = parse_items_page(
        {
            "data": [
                {
                    "id": "item-date",
                    "price": {"amount": 12.99, "currency": "EUR"},
                    "created_at": 1493118398000,
                    "modified_at": "2026-09-17T10:00:00Z",
                }
            ],
            "meta": {"next": None},
        },
        user_id="user-1",
    )
    item = page.items[0]
    assert item.price == Decimal("12.99")
    assert item.created_at is not None
    assert item.modified_at is not None
