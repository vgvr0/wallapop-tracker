import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.client import WallapopClient
from wallapop_tracker.domain.metadata import AvailableFilter
from wallapop_tracker.exceptions import WallapopParseError
from wallapop_tracker.parsers.brands import parse_brands
from wallapop_tracker.parsers.categories import parse_categories
from wallapop_tracker.parsers.filters import parse_available_filters
from wallapop_tracker.parsers.models import parse_models

BASE = "https://api.wallapop.com"
RAW = Path(__file__).parent / "fixtures" / "raw" / "2026-09"


def load(name: str) -> object:
    return json.loads((RAW / name).read_text(encoding="utf-8"))


def test_metadata_parsers_normalize_raw_fixtures():
    categories = parse_categories(load("categories.json"))
    assert categories[0].id == "100"
    assert categories[0].attribute_ids == ("brand", "model")
    assert categories[1].children[0].parent_id == "24200"

    filters = parse_available_filters(load("filters_regular.json"))
    assert [item.id for item in filters] == ["category", "is_shippable", "price", "condition"]
    assert filters[2].parameter_keys == ("min_sale_price", "max_sale_price")
    assert filters[3].options[0].name == "New"

    assert parse_brands(load("filters_brand.json"))[0].name == "Apple"
    assert parse_models(load("filters_model.json"))[1].name == "iPhone 15 Pro"


@pytest.mark.parametrize(
    "parser",
    [parse_categories, parse_available_filters, parse_brands, parse_models],
)
def test_metadata_parsers_accept_empty_collections(parser):
    payloads = {
        parse_categories: {"categories": []},
        parse_available_filters: {"filter_sections": []},
        parse_brands: {"id": "brand", "options": []},
        parse_models: {"id": "model", "options": []},
    }
    assert parser(payloads[parser]) == []


def test_filter_parser_preserves_supported_observed_types():
    result = parse_available_filters(load("filters_regular.json"))
    assert all(isinstance(item, AvailableFilter) for item in result)
    assert {item.filter_type for item in result} == {"tree", "toggle", "slider", "list"}


def test_metadata_parsers_preserve_unicode_and_numeric_ids():
    categories = parse_categories({"categories": [{"id": 7, "name": "Cámaras"}]})
    brands = parse_brands({"id": "brand", "options": [{"id": 8, "title": "Árbol"}]})
    assert categories[0].id == "7" and categories[0].name == "Cámaras"
    assert brands[0].id == "8" and brands[0].name == "Árbol"


@pytest.mark.parametrize(
    "parser,payload",
    [
        (parse_categories, {}),
        (parse_available_filters, {"filter_sections": [{}]}),
        (parse_brands, {"id": "brand", "options": [{}]}),
        (parse_models, {"id": "unexpected", "options": []}),
    ],
)
def test_metadata_parsers_reject_structurally_invalid_payloads(parser, payload):
    with pytest.raises(WallapopParseError):
        parser(payload)


@pytest.mark.asyncio
@respx.mock
async def test_client_metadata_endpoints_use_expected_requests():
    categories_route = respx.get(f"{BASE}/api/v3/categories").mock(
        return_value=httpx.Response(200, json=load("categories.json"))
    )
    filters_route = respx.get(f"{BASE}/api/v3/search/filters/regular-filters").mock(
        return_value=httpx.Response(200, json=load("filters_regular.json"))
    )
    brands_route = respx.get(f"{BASE}/api/v3/search/filters/brand").mock(
        return_value=httpx.Response(200, json=load("filters_brand.json"))
    )
    models_route = respx.get(f"{BASE}/api/v3/search/filters/model").mock(
        return_value=httpx.Response(200, json=load("filters_model.json"))
    )
    async with WallapopClient(min_interval=0) as client:
        assert (await client.categories())[0].name == "Cars"
        assert len(await client.available_filters(query="iphone", category_id="24200")) == 4
        assert (await client.brands(category_id="24200"))[0].name == "Apple"
        assert (await client.models(category_id="24200"))[0].name == "iPhone 15"
    assert categories_route.called
    assert filters_route.calls[0].request.url.params["keywords"] == "iphone"
    assert filters_route.calls[0].request.url.params["category_id"] == "24200"
    assert brands_route.calls[0].request.url.params["source"] == "search_box"
    assert models_route.called


@pytest.mark.asyncio
@respx.mock
async def test_client_metadata_raw_capture(tmp_path):
    respx.get(f"{BASE}/api/v3/categories").mock(
        return_value=httpx.Response(200, json=load("categories.json"))
    )
    async with WallapopClient(min_interval=0, raw_data_dir=tmp_path) as client:
        await client.categories()
    captured = list((tmp_path / "metadata").glob("*.json"))
    assert len(captured) == 1
    assert json.loads(captured[0].read_text(encoding="utf-8"))["categories"][0]["name"] == "Cars"


def test_metadata_cli_categories_and_brands(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def categories(self, **kwargs):
            return parse_categories(load("categories.json"))

        async def brands(self, **kwargs):
            return parse_brands(load("filters_brand.json"))[:2]

    monkeypatch.setattr(cli, "WallapopClient", FakeClient)
    runner = CliRunner()
    categories = runner.invoke(cli.app, ["metadata", "categories"])
    brands = runner.invoke(cli.app, ["metadata", "brands", "--category-id", "24200"])
    assert categories.exit_code == 0 and "Technology & electronics" in categories.output
    assert brands.exit_code == 0 and "Apple" in brands.output
