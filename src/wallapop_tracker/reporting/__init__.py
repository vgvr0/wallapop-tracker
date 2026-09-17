"""Public API for read-only historical reporting."""

from .metrics import WeeklyProfileSummary, get_average_active_price, get_weekly_summary
from .queries import (
    ApproxActiveDuration,
    InventoryListing,
    InventoryPoint,
    PresencePoint,
    PricePoint,
    ProfileMetricsPoint,
    get_approx_active_duration,
    get_current_inventory,
    get_inventory_history,
    get_new_listings_between_runs,
    get_presence_history,
    get_price_history,
    get_profile_metrics_history,
    get_removed_listings_between_runs,
)

__all__ = [
    "ApproxActiveDuration", "InventoryListing", "InventoryPoint", "PricePoint", "PresencePoint",
    "ProfileMetricsPoint", "WeeklyProfileSummary", "get_approx_active_duration",
    "average_active_price", "get_average_active_price", "get_current_inventory",
    "get_inventory_history",
    "get_new_listings", "get_new_listings_between_runs", "get_price_history",
    "get_presence_history", "get_profile_metrics_history", "get_removed_listings",
    "get_removed_listings_between_runs", "get_weekly_summary",
]

average_active_price = get_average_active_price
get_new_listings = get_new_listings_between_runs
get_removed_listings = get_removed_listings_between_runs
