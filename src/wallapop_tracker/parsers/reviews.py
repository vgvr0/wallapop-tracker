"""Public review summary parser."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from wallapop_tracker.models import ReviewSummary


def parse_review_summary(data: Mapping[str, Any]) -> ReviewSummary:
    rating_raw = data.get("average", data.get("rating"))
    rating = float(rating_raw) if isinstance(rating_raw, (int, float, Decimal)) else None
    count_raw = data.get("total_reviews", data.get("review_count"))
    review_count = int(count_raw) if isinstance(count_raw, (int, float, Decimal)) else None
    breakdown_raw = data.get("breakdown")
    distribution = None
    if isinstance(breakdown_raw, Mapping):
        distribution = {
            int(stars): int(value.get("percentage", 0))
            for stars, value in breakdown_raw.items()
            if str(stars).isdigit() and isinstance(value, Mapping)
        }
    return ReviewSummary(rating=rating, review_count=review_count, rating_distribution=distribution)
