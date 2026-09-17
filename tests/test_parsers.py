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
    assert profile.location_city == "Madrid"
    assert profile.postal_code == "28001"
    assert profile.seller_type == "Private"
    assert profile.verified is True
    assert profile.registered_at.tzinfo == UTC


def test_parse_stats(fixture_data):
    stats = parse_profile_stats(fixture_data("stats.json"))
    assert stats.rating == 4.5
    assert stats.review_count == 12
    assert stats.published_count == 3
    assert stats.sold_count == 7
    assert stats.purchases_count is None
    assert stats.sales_count is None
    assert stats.reports_count is None


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


def test_parse_raw_2026_extension_fields():
    import json
    from pathlib import Path

    raw_dir = Path(__file__).parent / "fixtures" / "raw" / "2026-09"
    profile = parse_profile(json.loads((raw_dir / "profile.json").read_text(encoding="utf-8")))
    stats = parse_profile_stats(json.loads((raw_dir / "stats.json").read_text(encoding="utf-8")))
    page = parse_items_page(
        json.loads((raw_dir / "items_page_1.json").read_text(encoding="utf-8")),
        user_id=profile.user_id,
    )

    assert profile.location_city == "Roquetas de Mar"
    assert profile.postal_code == "04740"
    assert profile.country_code == "ES"
    assert profile.is_top_profile is False
    assert stats.purchases_count == 14
    assert stats.sales_count == 417
    assert stats.sold_count == 415
    assert stats.reports_count == 10
    first = page.items[0]
    assert first.shipping_available is True
    assert first.seller_allows_shipping is True
    assert first.condition == "un_opened"
    assert first.brand == "Other"
    assert first.has_warranty is False
    assert first.is_refurbished is False
    assert first.images_json and first.images_json[0]["id"]
    assert first.attributes_json and "condition" in first.attributes_json


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


def test_images_json_excludes_average_color():
    page = parse_items_page(
        {
            "data": [
                {
                    "id": "image-item",
                    "images": [
                        {
                            "id": "image-1",
                            "average_color": "13C1AC",
                            "average_hex_color": "#13C1AC",
                            "urls": {"small": "small-url", "big": "big-url"},
                            "width": 800,
                            "height": 600,
                        }
                    ],
                }
            ],
            "meta": {"next": None},
        },
        user_id="user-1",
    )
    image = page.items[0].images_json[0]
    assert image == {
        "id": "image-1",
        "urls": {"small": "small-url", "big": "big-url"},
        "width": 800,
        "height": 600,
    }
