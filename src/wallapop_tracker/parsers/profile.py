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
    location_city = None
    postal_code = None
    country_code = None
    if isinstance(location_raw, Mapping):
        location_city = (
            location_raw.get("city") if isinstance(location_raw.get("city"), str) else None
        )
        postal_code = (
            location_raw.get("zip") if isinstance(location_raw.get("zip"), str) else None
        )
        country_code = (
            location_raw.get("country_code")
            if isinstance(location_raw.get("country_code"), str)
            else None
        )
        location = (
            ", ".join(str(value) for key in ("city", "zip") if (value := location_raw.get(key)))
            or None
        )
    elif isinstance(location_raw, str):
        location = location_raw
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
        location_city=location_city,
        postal_code=postal_code,
        country_code=country_code,
        registered_at=parse_timestamp(first_value(raw, "register_date", "registerDate")),
        seller_type=seller_type if isinstance(seller_type, str) else None,
        verified=verified if isinstance(verified, bool) else None,
        is_top_profile=raw.get("is_top_profile")
        if isinstance(raw.get("is_top_profile"), bool)
        else None,
    )
