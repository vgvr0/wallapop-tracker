"""Profile statistics parser."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from wallapop_tracker.models import ProfileStats


def _values(data: Mapping[str, Any], key: str) -> dict[str, int]:
    raw = data.get(key, {})
    if isinstance(raw, Mapping):
        return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float, Decimal))}
    if isinstance(raw, list):
        return {
            str(row["type"]): int(row["value"])
            for row in raw
            if isinstance(row, Mapping)
            and isinstance(row.get("type"), str)
            and isinstance(row.get("value"), (int, float, Decimal))
        }
    return {}


def parse_profile_stats(data: Mapping[str, Any]) -> ProfileStats:
    ratings = _values(data, "ratings")
    counters = _values(data, "counters")
    rating_raw = data.get("rating_average", data.get("ratingAverage"))
    rating = float(rating_raw) if isinstance(rating_raw, (int, float, Decimal)) else None
    return ProfileStats(
        rating=rating,
        review_count=counters.get("reviews", ratings.get("reviews")),
        published_count=counters.get("publish"),
        purchases_count=counters.get("buys"),
        sales_count=counters.get("sells"),
        # ``sold`` is an explicit counter in the current API; do not derive it.
        sold_count=counters.get("sold"),
        reports_count=counters.get("reports_received"),
    )
