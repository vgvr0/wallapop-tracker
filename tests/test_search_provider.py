from decimal import Decimal

import httpx
import pytest
import respx

from wallapop_tracker.client import WallapopClient
from wallapop_tracker.providers.search import SearchRequest, WallapopSearchProvider

BASE = "https://api.wallapop.com"


def page(ids: list[str], next_page: str | None = None) -> dict:
    return {
        "data": {
            "items": [
                {
                    "id": item_id,
                    "user_id": "seller",
                    "title": item_id,
                    "brand": "Acme",
                    "condition": "new",
                    "price": {"amount": 100, "currency": "EUR"},
                    "web_slug": item_id,
                    "shipping": {"item_is_shippable": True},
                }
                for item_id in ids
            ]
        },
        "meta": {"next_page": next_page},
    }


@pytest.mark.asyncio
@respx.mock
async def test_wallapop_provider_paginates_and_deduplicates():
    route = respx.get(f"{BASE}/api/v3/search").mock(
        side_effect=[
            httpx.Response(200, json=page(["A", "B"], "cursor-1")),
            httpx.Response(200, json=page(["B", "C"])),
        ]
    )
    request = SearchRequest(query="phone", min_price=Decimal("50"), max_pages=5)

    async with WallapopClient(min_interval=0) as client:
        listings = await WallapopSearchProvider(client).search(request)

    assert [listing.item_id for listing in listings] == ["A", "B", "C"]
    assert route.call_count == 2
    assert route.calls[0].request.url.params["keywords"] == "phone"
    assert route.calls[1].request.url.params["next_page"] == "cursor-1"


@pytest.mark.asyncio
@respx.mock
async def test_wallapop_provider_respects_max_pages_and_optional_fields():
    route = respx.get(f"{BASE}/api/v3/search").mock(
        side_effect=[
            httpx.Response(200, json=page(["A"], "cursor-1")),
            httpx.Response(200, json=page(["B"], "cursor-2")),
        ]
    )
    request = SearchRequest(
        query="phone",
        category_id="cat-1",
        condition="new",
        brand="Acme",
        shipping_required=True,
        latitude=40.4,
        longitude=-3.7,
        distance=20,
        max_pages=1,
    )

    async with WallapopClient(min_interval=0) as client:
        listings = await WallapopSearchProvider(client).search(request)

    assert [listing.item_id for listing in listings] == ["A"]
    assert route.call_count == 1
    params = route.calls[0].request.url.params
    assert params["category_id"] == "cat-1"
    assert params["latitude"] == "40.4"
    assert params["longitude"] == "-3.7"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("payload", [{"data": {}}, {"unexpected": True}])
async def test_wallapop_provider_handles_empty_or_unexpected_payload(payload):
    respx.get(f"{BASE}/api/v3/search").mock(return_value=httpx.Response(200, json=payload))

    async with WallapopClient(min_interval=0) as client:
        listings = await WallapopSearchProvider(client).search(SearchRequest(query="phone"))

    assert listings == []
