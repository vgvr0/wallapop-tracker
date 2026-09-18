from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class AlertType(StrEnum):
    NEW_SEARCH_MATCH = "NEW_SEARCH_MATCH"
    NEW_LISTING = "NEW_LISTING"
    PRICE_DROP = "PRICE_DROP"
    PRICE_INCREASE = "PRICE_INCREASE"
    REMOVED = "REMOVED"
    REAPPEARED = "REAPPEARED"
    TITLE_CHANGE = "TITLE_CHANGE"
    RESERVATION_CHANGE = "RESERVATION_CHANGE"
    SHIPPING_CHANGE = "SHIPPING_CHANGE"
    STATUS_CHANGE = "STATUS_CHANGE"


@dataclass(frozen=True)
class SearchMatchAlert:
    type: AlertType
    created_at: datetime
    listing_id: str
    saved_search_id: int
    old_price: Decimal | None
    new_price: Decimal | None
    title: str | None
    url: str | None


@dataclass(frozen=True)
class PriceDropAlert:
    type: AlertType
    created_at: datetime
    listing_id: int
    saved_search_id: int | None
    old_price: Decimal
    new_price: Decimal
    title: str | None
    url: str | None


@dataclass(frozen=True)
class TrackingAlert:
    """Persisted, globally deduplicated alert emitted by a tracker."""

    event_id: int
    type: AlertType
    created_at: datetime
    listing_id: str
    tracked_search_id: int | None
    old_price: Decimal | None
    new_price: Decimal | None
    title: str | None
    url: str | None
    idempotency_key: str
    tracked_listing_id: int | None = None
