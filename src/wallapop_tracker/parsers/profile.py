"""Profile response parser."""

from collections.abc import Mapping
from typing import Any

from wallapop_tracker.exceptions import WallapopParseError
from wallapop_tracker.models import Profile

from .common import first_value, parse_timestamp


def parse_profile(data: Mapping[str, Any]) -> Profile:
    raw = dict(data)
    user_id = first_value(raw, "id", "user_id")
    if not isinstance(user_id, str) or not user_id:
        raise WallapopParseError("Profile response has no user id")
    location_raw = first_value(raw, "location")
    location = None
    if isinstance(location_raw, Mapping):
        location = (
            ", ".join(str(value) for key in ("city", "zip") if (value := location_raw.get(key)))
            or None
        )
    elif isinstance(location_raw, str):
        location = location_raw
    image_raw = first_value(raw, "image", "avatarImage", "avatar_image")
    image_url = image_raw if isinstance(image_raw, str) else None
    if isinstance(image_raw, Mapping):
        urls = image_raw.get("urls_by_size") or image_raw.get("urls") or {}
        image_url = first_value(dict(urls), "medium", "large", "original", "small")
    seller_raw = first_value(raw, "seller_type", "sellerType")
    seller_type = seller_raw.get("type") if isinstance(seller_raw, Mapping) else seller_raw
    verified = (
        seller_raw.get("verified") if isinstance(seller_raw, Mapping) else raw.get("verified")
    )
    return Profile(
        user_id=user_id,
        name=first_value(raw, "micro_name", "microName", "name"),
        slug=first_value(raw, "web_slug", "webSlug", "slug"),
        url=first_value(raw, "url_share", "urlShare", "url"),
        location=location,
        image_url=image_url,
        registered_at=parse_timestamp(first_value(raw, "register_date", "registerDate")),
        seller_type=seller_type if isinstance(seller_type, str) else None,
        verified=verified if isinstance(verified, bool) else None,
    )
