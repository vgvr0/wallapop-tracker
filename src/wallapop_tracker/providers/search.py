"""Search provider contracts and the Wallapop implementation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from wallapop_tracker.client import WallapopClient
from wallapop_tracker.models import Listing


@dataclass(frozen=True)
class SearchRequest:
    """Marketplace-independent semantics for one search operation."""

    query: str
    category_id: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    condition: str | None = None
    brand: str | None = None
    shipping_required: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    distance: float | None = None
    max_pages: int = 5


class SearchProvider(Protocol):
    """Provider contract consumed by ``SearchTracker``."""

    async def search(self, request: SearchRequest) -> list[Listing]:
        """Return normalized listing candidates for a semantic request."""


class WallapopSearchProvider:
    """Adapt the current Wallapop search primitive to ``SearchProvider``."""

    def __init__(self, client: WallapopClient) -> None:
        self.client = client

    async def search(self, request: SearchRequest) -> list[Listing]:
        """Delegate HTTP, pagination and response normalization to the client."""
        return await self.client.search_items(
            query=request.query,
            category_id=request.category_id,
            min_price=request.min_price,
            max_price=request.max_price,
            condition=request.condition,
            brand=request.brand,
            shipping_required=request.shipping_required,
            latitude=request.latitude,
            longitude=request.longitude,
            distance=request.distance,
            max_pages=request.max_pages,
        )

