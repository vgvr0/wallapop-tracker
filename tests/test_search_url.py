import json
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.parsers.search_url import SearchURLParseError, parse_search_url
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackedSearchRecord

BASE = "https://es.wallapop.com/app/search"


def test_parse_query_unicode_and_encoding():
    result = parse_search_url(f"{BASE}?keywords=caf%C3%A9+con+leche")
    assert result.query == "café con leche"


def test_parse_all_supported_semantic_parameters():
    result = parse_search_url(
        f"{BASE}?keywords=iphone&category_id=24200&min_sale_price=300"
        "&max_sale_price=650&latitude=40.42&longitude=-3.70&distance=20"
        "&shipping=true&condition=used&brand=Apple"
    )
    assert result == result.__class__(
        query="iphone",
        category_id="24200",
        min_price=Decimal("300"),
        max_price=Decimal("650"),
        latitude=40.42,
        longitude=-3.70,
        distance=20.0,
        shipping_required=True,
        condition="used",
        brand="Apple",
        unknown_params={},
        ignored_params=(),
    )


def test_parse_false_shipping_and_aliases():
    result = parse_search_url(
        f"{BASE}?kws=camara&catIds=12800&minPrice=10&maxPrice=20&dist=5&shipping=0"
    )
    assert result.query == "camara"
    assert result.category_id == "12800"
    assert result.min_price == Decimal("10")
    assert result.max_price == Decimal("20")
    assert result.distance == 5
    assert result.shipping_required is False


def test_unknown_and_internal_parameters_are_distinguished():
    result = parse_search_url(
        f"{BASE}?keywords=phone&foo=one&foo=two&next_page=cursor&order_by=newest"
    )
    assert result.unknown_params == {"foo": ("one", "two")}
    assert result.ignored_params == ("next_page", "order_by")


def test_repeated_identical_parameter_is_allowed():
    assert parse_search_url(f"{BASE}?keywords=phone&keywords=phone").query == "phone"


def test_repeated_conflicting_parameter_is_rejected():
    with pytest.raises(SearchURLParseError, match="conflicting"):
        parse_search_url(f"{BASE}?keywords=phone&keywords=tablet")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://example.com/app/search?keywords=phone", "Wallapop host"),
        ("https://es.wallapop.com/item/phone-1", "not a search"),
        ("not a url", "HTTPS"),
        (f"{BASE}?keywords=phone&min_price=bad", "decimal"),
        (f"{BASE}?keywords=phone&latitude=91", "latitude"),
        (f"{BASE}?keywords=phone&longitude=-181", "longitude"),
        (f"{BASE}?keywords=phone&distance=0", "greater than zero"),
        (f"{BASE}?keywords=phone&shipping=maybe", "boolean"),
        (f"{BASE}?keywords=phone&min_price=20&max_price=10", "must not exceed"),
    ],
)
def test_invalid_urls_are_rejected(url: str, message: str):
    with pytest.raises(SearchURLParseError, match=message):
        parse_search_url(url)


def test_empty_query_is_explicitly_represented_as_none():
    assert parse_search_url(f"{BASE}?keywords=+").query is None


def test_search_filters_are_json_compatible():
    result = parse_search_url(f"{BASE}?keywords=phone&category_id=1&latitude=40&shipping=false")
    assert json.dumps(result.search_filters())


def test_imported_configuration_matches_manual_search_request():
    imported = parse_search_url(
        f"{BASE}?keywords=phone&category_id=24200&min_price=300&max_price=650"
        "&latitude=40.42&longitude=-3.70&distance=20&shipping=true&condition=used&brand=Apple"
    )
    manual = SearchRequest(
        query="phone",
        category_id="24200",
        min_price=Decimal("300"),
        max_price=Decimal("650"),
        latitude=40.42,
        longitude=-3.70,
        distance=20,
        shipping_required=True,
        condition="used",
        brand="Apple",
    )
    imported_request = SearchRequest(
        query=imported.query or "",
        category_id=imported.category_id,
        min_price=imported.min_price,
        max_price=imported.max_price,
        latitude=imported.latitude,
        longitude=imported.longitude,
        distance=imported.distance,
        shipping_required=imported.shipping_required,
        condition=imported.condition,
        brand=imported.brand,
    )
    assert imported_request == manual


def test_search_import_cli_creates_record_and_warns_unknown(tmp_path, monkeypatch):
    path = tmp_path / "import.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.close()

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "search",
            "import",
            f"{BASE}?keywords=iphone+15&min_sale_price=300&max_sale_price=650&foo=bar",
            "--name",
            "iPhone barato",
            "--interval-seconds",
            "900",
            "--disabled",
            "--notify-on-first-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Search created" in result.output
    assert "Ignored unsupported parameters:" in result.output
    assert "- foo" in result.output
    with Database(f"sqlite:///{path}").session() as session:
        record = session.get(TrackedSearchRecord, 1)
        assert record is not None
        assert record.query == "iphone 15"
        assert record.name == "iPhone barato"
        assert record.interval_seconds == 900
        assert record.enabled is False
        assert record.notify_on_first_run is True
        assert record.min_price == Decimal("300")
