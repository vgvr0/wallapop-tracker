"""Stable domain models, intentionally smaller than Wallapop's JSON payloads."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wallapop_tracker.domain.marketplace import Marketplace


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    name: str | None = None
    slug: str | None = None
    url: str | None = None
    location: str | None = None
    location_city: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    registered_at: datetime | None = None
    seller_type: str | None = None
    verified: bool | None = None
    is_top_profile: bool | None = None


class ProfileStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rating: float | None = None
    review_count: int | None = None
    published_count: int | None = None
    purchases_count: int | None = None
    sales_count: int | None = None
    sold_count: int | None = None
    reports_count: int | None = None


class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rating: float | None = None
    review_count: int | None = None
    rating_distribution: dict[int, int] | None = None


class Listing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: str
    marketplace: Marketplace = Marketplace.WALLAPOP
    user_id: str
    title: str | None = None
    description: str | None = None
    price: Decimal | None = Field(default=None)
    currency: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    status: str | None = None
    reserved: bool | None = None
    shipping_available: bool | None = None
    seller_allows_shipping: bool | None = None
    condition: str | None = None
    condition_code: str | None = None
    condition_label: str | None = None
    brand: str | None = None
    has_warranty: bool | None = None
    is_refurbished: bool | None = None
    url: str | None = None
    image_url: str | None = None
    images_json: list[dict[str, Any]] | None = None
    attributes_json: dict[str, Any] | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None

    @property
    def external_id(self) -> str:
        """Marketplace-neutral alias retained alongside the legacy item_id."""
        return self.item_id


class ItemsPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[Listing]
    next_since: str | None = None
