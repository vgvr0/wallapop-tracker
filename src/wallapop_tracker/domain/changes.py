"""In-memory change events emitted by the historical diff engine."""

from dataclasses import dataclass
from enum import StrEnum


class ChangeType(StrEnum):
    """Supported changes; these are deliberately not persistence events yet."""

    NEW_LISTING = "NEW_LISTING"
    REMOVED = "REMOVED"
    REAPPEARED = "REAPPEARED"
    PRICE_CHANGED = "PRICE_CHANGED"
    TITLE_CHANGED = "TITLE_CHANGED"
    RESERVED = "RESERVED"
    UNRESERVED = "UNRESERVED"
    REVIEW_COUNT_CHANGED = "REVIEW_COUNT_CHANGED"
    RATING_CHANGED = "RATING_CHANGED"
    SOLD_COUNT_CHANGED = "SOLD_COUNT_CHANGED"


@dataclass(frozen=True)
class DetectedChange:
    """A single deterministic, non-persisted domain change."""

    change_type: ChangeType
    profile_id: int
    listing_id: int | None
    previous_run_id: int | None
    current_run_id: int
    old_value: object | None
    new_value: object | None
