"""Provider contract for fetching one normalized listing."""

from typing import Protocol

from wallapop_tracker.client import WallapopClient
from wallapop_tracker.models import Listing


class ListingProvider(Protocol):
    async def get(self, item_id: str) -> Listing:
        """Return the current normalized listing or raise a typed client error."""


class WallapopListingProvider:
    def __init__(self, client: WallapopClient) -> None:
        self.client = client

    async def get(self, item_id: str) -> Listing:
        return await self.client.get_item(item_id)
