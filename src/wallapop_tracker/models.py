"""Stable domain models, intentionally smaller than Wallapop's JSON payloads."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    name: str | None = None
    slug: str | None = None
    url: str | None = None
    location: str | None = None
    image_url: str | None = None
    registered_at: datetime | None = None
    seller_type: str | None = None
    verified: bool | None = None


class ProfileStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rating: float | None = None
    review_count: int | None = None
    published_count: int | None = None
    sold_count: int | None = None


class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rating: float | None = None
    review_count: int | None = None
    rating_distribution: dict[int, int] | None = None


class Listing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: str
    user_id: str
    title: str | None = None
    description: str | None = None
    price: Decimal | None = Field(default=None)
    currency: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    status: str | None = None
    reserved: bool | None = None
    url: str | None = None
    image_url: str | None = None
    created_at: datetime | None = None
    modified_at: datetime | None = None


class ItemsPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[Listing]
    next_since: str | None = None
