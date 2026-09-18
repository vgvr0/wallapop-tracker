"""Public API for read-only historical reporting."""

from .metrics import WeeklyProfileSummary, get_average_active_price, get_weekly_summary
from .queries import (
    ApproxActiveDuration,
    InventoryListing,
    InventoryPoint,
    PresencePoint,
    PricePoint,
    ProfileMetricsPoint,
    SearchTrackingMetrics,
    get_approx_active_duration,
    get_current_inventory,
    get_inventory_history,
    get_new_listings_between_runs,
    get_possible_relistings,
    get_presence_history,
    get_price_history,
    get_profile_metrics_history,
    get_relisting_candidates_for_listing,
    get_removed_listings_between_runs,
    get_search_tracking_metrics,
)

__all__ = [
    "ApproxActiveDuration", "InventoryListing", "InventoryPoint", "PricePoint", "PresencePoint",
    "ProfileMetricsPoint", "WeeklyProfileSummary", "get_approx_active_duration",
    "SearchTrackingMetrics", "get_search_tracking_metrics",
    "get_possible_relistings", "get_relisting_candidates_for_listing",
    "average_active_price", "get_average_active_price", "get_current_inventory",
    "get_inventory_history",
    "get_new_listings", "get_new_listings_between_runs", "get_price_history",
    "get_presence_history", "get_profile_metrics_history", "get_removed_listings",
    "get_removed_listings_between_runs", "get_weekly_summary",
]

average_active_price = get_average_active_price
get_new_listings = get_new_listings_between_runs
get_removed_listings = get_removed_listings_between_runs
