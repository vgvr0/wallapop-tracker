"""Published-listing response parser."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from wallapop_tracker.exceptions import WallapopParseError
from wallapop_tracker.models import ItemsPage, Listing

from .common import first_value, parse_timestamp


def _image_url(raw: Any) -> str | None:
    if not isinstance(raw, list) or not raw:
        return None
    first = raw[0]
    if not isinstance(first, Mapping):
        return None
    urls = first.get("urls") or first.get("urls_by_size") or {}
    result = first_value(dict(urls), "medium", "large", "big", "original", "small")
    return result if isinstance(result, str) else None


def _listing(raw: Mapping[str, Any], user_id: str) -> Listing | None:
    item_id = first_value(dict(raw), "id", "item_id")
    if not isinstance(item_id, str) or not item_id:
        return None
    price_raw = raw.get("price")
    price = None
    currency = None
    if isinstance(price_raw, Mapping):
        amount = price_raw.get("amount")
        if isinstance(amount, (int, float, str, Decimal)):
            price = Decimal(str(amount))
        currency = price_raw.get("currency") if isinstance(price_raw.get("currency"), str) else None
    reserved_raw = first_value(dict(raw), "isReserved", "reserved")
    if isinstance(reserved_raw, Mapping):
        reserved_raw = reserved_raw.get("flag")
    url = first_value(dict(raw), "url", "web_url")
    if not isinstance(url, str):
        slug = raw.get("slug")
        url = f"https://www.wallapop.com/item/{slug}" if isinstance(slug, str) else None
    return Listing(
        item_id=item_id,
        user_id=user_id,
        title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        description=raw.get("description") if isinstance(raw.get("description"), str) else None,
        price=price,
        currency=currency,
        category_id=str(raw["category_id"])
        if raw.get("category_id") is not None
        else (str(raw["categoryId"]) if raw.get("categoryId") is not None else None),
        status=raw.get("status") if isinstance(raw.get("status"), str) else None,
        reserved=reserved_raw if isinstance(reserved_raw, bool) else None,
        url=url,
        image_url=_image_url(raw.get("images")),
        created_at=parse_timestamp(first_value(dict(raw), "created_at", "createdAt")),
        modified_at=parse_timestamp(
            first_value(dict(raw), "modified_at", "modifiedAt", "updated_at")
        ),
    )


def parse_items_page(data: Mapping[str, Any], *, user_id: str) -> ItemsPage:
    raw_items = data.get("data")
    if not isinstance(raw_items, list):
        raise WallapopParseError("Items response has no data list")
    items: list[Listing] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise WallapopParseError("Items response contains a non-object item")
        parsed = _listing(raw, user_id)
        if parsed is None:
            import logging

            logging.getLogger(__name__).warning("skip_item_without_id user_id=%s", user_id)
            continue
        items.append(parsed)
    meta = data.get("meta")
    next_since = meta.get("next") if isinstance(meta, Mapping) else None
    return ItemsPage(items=items, next_since=next_since if isinstance(next_since, str) else None)
