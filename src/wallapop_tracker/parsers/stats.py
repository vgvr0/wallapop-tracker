"""Profile statistics parser."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from wallapop_tracker.exceptions import WallapopParseError
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
    reports_received = _reports_received(data)
    return ProfileStats(
        rating=rating,
        review_count=counters.get("reviews", ratings.get("reviews")),
        published_count=counters.get("publish"),
        purchases_count=counters.get("buys"),
        sales_count=counters.get("sells"),
        # ``sold`` is an explicit counter in the current API; do not derive it.
        sold_count=counters.get("sold"),
        reports_received=reports_received,
    )


def _reports_received(data: Mapping[str, Any]) -> int | None:
    raw = data.get("counters", {})
    value: Any = None
    present = False
    if isinstance(raw, Mapping):
        present = "reports_received" in raw
        value = raw.get("reports_received")
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, Mapping) and row.get("type") == "reports_received":
                present = True
                value = row.get("value")
                break
    if not present or value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WallapopParseError("stats.reports_received must be a non-negative integer or null")
    return value
