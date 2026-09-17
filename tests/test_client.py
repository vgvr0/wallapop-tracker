import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from wallapop_tracker.client import WallapopClient
from wallapop_tracker.exceptions import (
    WallapopHTTPError,
    WallapopNotFoundError,
    WallapopPaginationError,
)

BASE = "https://api.wallapop.com"


def search_page(ids: list[str], next_page: str | None = None, **overrides: object) -> dict:
    items = []
    for item_id in ids:
        value = {
            "id": item_id,
            "user_id": "seller",
            "title": item_id,
            "price": {"amount": 100, "currency": "EUR"},
            "web_slug": item_id,
            **overrides,
        }
        items.append(value)
    return {"data": {"items": items}, "meta": {"next_page": next_page}}


async def run_search(responses: list[dict], **kwargs):
    route = respx.get(f"{BASE}/api/v3/search").mock(
        side_effect=[httpx.Response(200, json=response) for response in responses]
    )
    async with WallapopClient(min_interval=0) as client:
        result = await client.search_items(keywords="phone", **kwargs)
    return result, route


@pytest.mark.asyncio
@respx.mock
async def test_search_multiple_pages():
    items, route = await run_search([search_page(["A", "B"], "cursor-1"), search_page(["C", "D"])])
    assert [item.item_id for item in items] == ["A", "B", "C", "D"]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["next_page"] == "cursor-1"


@pytest.mark.asyncio
@respx.mock
async def test_search_pagination_stops():
    items, route = await run_search([search_page(["A"])])
    assert [item.item_id for item in items] == ["A"]
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_repeated_cursor():
    items, route = await run_search(
        [search_page(["A"], "cursor-1"), search_page(["B"], "cursor-1")]
    )
    assert [item.item_id for item in items] == ["A", "B"]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_search_duplicate_items_across_pages():
    items, _ = await run_search([search_page(["A", "B"], "cursor-1"), search_page(["B", "C"])])
    assert [item.item_id for item in items] == ["A", "B", "C"]


@pytest.mark.asyncio
@respx.mock
async def test_max_pages_limit():
    items, route = await run_search(
        [search_page(["A"], "cursor-1"), search_page(["B"], "cursor-2"), search_page(["C"])],
        max_pages=2,
    )
    assert [item.item_id for item in items] == ["A", "B"]
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_local_filters_apply_after_pagination():
    page_one = search_page(
        ["wrong"],
        "cursor-1",
        price={"amount": 10},
        brand="Other",
        condition="used",
        shipping={"item_is_shippable": False},
    )
    page_two = search_page(
        ["valid"],
        price={"amount": 100},
        brand="Acme",
        condition="new",
        shipping={"item_is_shippable": True},
    )
    items, route = await run_search(
        [page_one, page_two],
        min_price=Decimal("50"),
        max_price=Decimal("150"),
        brand="Acme",
        condition="new",
        shipping_required=True,
    )
    assert [item.item_id for item in items] == ["valid"]
    assert route.call_count == 2


def load_fixture(name: str):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
@respx.mock
async def test_items_pagination_and_duplicate_deduplication():
    first = load_fixture("items_page_1.json")
    second = load_fixture("items_page_2.json")
    respx.get(f"{BASE}/api/v3/users/user-1/items").mock(
        side_effect=[
            httpx.Response(200, json=first),
            httpx.Response(
                200,
                json={"data": [first["data"][0], *second["data"]], "meta": {"next": None}},
            ),
        ]
    )
    async with WallapopClient(min_interval=0) as client:
        items = await client.get_all_items("user-1")
    assert [item.item_id for item in items] == ["item-1", "item-2"]
    assert "since" not in str(respx.calls[0].request.url)
    assert respx.calls[1].request.url.params["since"] == "cursor-1"


@pytest.mark.asyncio
@respx.mock
async def test_items_pagination_requests_first_second_and_final_pages():
    first = load_fixture("items_page_1.json")
    second = load_fixture("items_page_2.json")
    final = load_fixture("items_page_3.json")
    route = respx.get(f"{BASE}/api/v3/users/user-1/items").mock(
        side_effect=[
            httpx.Response(200, json=first),
            httpx.Response(200, json=second),
            httpx.Response(200, json=final),
        ]
    )
    async with WallapopClient(min_interval=0) as client:
        items = await client.get_all_items("user-1")
    assert [str(call.request.url) for call in route.calls] == [
        f"{BASE}/api/v3/users/user-1/items",
        f"{BASE}/api/v3/users/user-1/items?since=cursor-1",
        f"{BASE}/api/v3/users/user-1/items?since=cursor-2",
    ]
    assert [item.item_id for item in items] == ["item-1", "item-2"]


@pytest.mark.asyncio
@respx.mock
async def test_repeated_cursor_detection():
    payload = load_fixture("items_page_1.json")
    respx.get(f"{BASE}/api/v3/users/user-1/items").mock(
        return_value=httpx.Response(200, json=payload)
    )
    async with WallapopClient(min_interval=0) as client:
        with pytest.raises(WallapopPaginationError):
            await client.get_all_items("user-1")


@pytest.mark.asyncio
@respx.mock
async def test_retry_429_then_success():
    route = respx.get(f"{BASE}/api/v3/users/user-1/stats").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=load_fixture("stats.json")),
        ]
    )
    async with WallapopClient(min_interval=0, backoff_factor=0) as client:
        backoff = AsyncMock(wraps=client._backoff)
        client._backoff = backoff
        stats = await client.get_profile_stats("user-1")
    backoff.assert_awaited_once_with(0, "0")
    assert stats.review_count == 12
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_retry_500_then_success():
    route = respx.get(f"{BASE}/api/v3/users/user-1/stats").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=load_fixture("stats.json")),
        ]
    )
    async with WallapopClient(min_interval=0, backoff_factor=0) as client:
        await client.get_profile_stats("user-1")
    assert route.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [502, 503, 504])
@respx.mock
async def test_retry_other_transient_statuses(status):
    route = respx.get(f"{BASE}/api/v3/users/user-1/stats").mock(
        side_effect=[httpx.Response(status), httpx.Response(200, json=load_fixture("stats.json"))]
    )
    async with WallapopClient(min_interval=0, backoff_factor=0) as client:
        await client.get_profile_stats("user-1")
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_retry_after_is_capped(monkeypatch):
    route = respx.get(f"{BASE}/api/v3/users/user-1/stats").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "999"}),
            httpx.Response(200, json=load_fixture("stats.json")),
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr("wallapop_tracker.client.asyncio.sleep", sleep)
    async with WallapopClient(min_interval=0, backoff_factor=0) as client:
        await client.get_profile_stats("user-1")
    assert route.call_count == 2
    assert sleep.await_args_list[0].args == (60.0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403])
@respx.mock
async def test_no_retry_permanent_statuses(status):
    route = respx.get(f"{BASE}/api/v3/users/user-1/stats").mock(return_value=httpx.Response(status))
    async with WallapopClient(min_interval=0) as client:
        with pytest.raises(WallapopHTTPError):
            await client.get_profile_stats("user-1")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_not_found_404():
    respx.get(f"{BASE}/api/v3/users/nope").mock(return_value=httpx.Response(404))
    async with WallapopClient(min_interval=0) as client:
        with pytest.raises(WallapopNotFoundError):
            await client.get_profile("nope")


@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_id_from_next_data():
    html = '<script id="__NEXT_DATA__">{"props":{"pageProps":{"user":{"id":"user-1"}}}}</script>'
    respx.get("https://es.wallapop.com/user/ana-1/").mock(
        return_value=httpx.Response(200, text=html)
    )
    async with WallapopClient(min_interval=0) as client:
        assert await client.resolve_user_id("https://es.wallapop.com/user/ana-1/") == "user-1"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_user_id_locally_without_http():
    async with WallapopClient(min_interval=0) as client:
        assert (
            await client.resolve_user_id(
                "https://www.wallapop.com/user/example-v4z4nyeyq8jy?tab=items"
            )
            == "v4z4nyeyq8jy"
        )


@pytest.mark.asyncio
@respx.mock
async def test_raw_data_is_optional_and_written_before_normalization(tmp_path):
    payload = {"id": "user-1", "micro_name": "Ana"}
    respx.get(f"{BASE}/api/v3/users/user-1").mock(return_value=httpx.Response(200, json=payload))
    async with WallapopClient(min_interval=0) as client:
        await client.get_profile("user-1")
    assert not (tmp_path / "profile").exists()

    respx.get(f"{BASE}/api/v3/users/user-1").mock(return_value=httpx.Response(200, json=payload))
    async with WallapopClient(min_interval=0, raw_data_dir=tmp_path) as client:
        await client.get_profile("user-1")
    raw_files = list((tmp_path / "profile").glob("*.json"))
    assert len(raw_files) == 1
    assert json.loads(raw_files[0].read_text(encoding="utf-8")) == payload


@pytest.mark.asyncio
@respx.mock
async def test_raw_filesystem_error_does_not_break_scraping(tmp_path):
    broken_path = tmp_path / "raw-file"
    broken_path.write_text("not a directory", encoding="utf-8")
    respx.get(f"{BASE}/api/v3/users/user-1").mock(
        return_value=httpx.Response(200, json={"id": "user-1"})
    )
    async with WallapopClient(min_interval=0, raw_data_dir=broken_path) as client:
        profile = await client.get_profile("user-1")
    assert profile.user_id == "user-1"
