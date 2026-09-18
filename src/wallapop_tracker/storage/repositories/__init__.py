"""Compatibility package exposing the storage repositories.

The repository implementation lives in the sibling module so the public import
path remains ``wallapop_tracker.storage.repositories`` even if a package cache
from an earlier development checkout is present.
"""

import importlib.util
import sys
from pathlib import Path

_implementation_path = Path(__file__).resolve().parent.parent / "repositories.py"
_spec = importlib.util.spec_from_file_location(
    "wallapop_tracker.storage._repositories_impl", _implementation_path
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load repository implementation: {_implementation_path}")
_implementation = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _implementation
_spec.loader.exec_module(_implementation)

ProfileRepository = _implementation.ProfileRepository
ListingRepository = _implementation.ListingRepository
SnapshotRepository = _implementation.SnapshotRepository
TrackingRunRepository = _implementation.TrackingRunRepository
TrackedProfileRepository = _implementation.TrackedProfileRepository
TrackedListingRepository = _implementation.TrackedListingRepository
TrackedSearchRepository = _implementation.TrackedSearchRepository
SearchMatchRepository = _implementation.SearchMatchRepository
TrackingEventRepository = _implementation.TrackingEventRepository
NotificationDeliveryRepository = _implementation.NotificationDeliveryRepository
PossibleRelistingRepository = _implementation.PossibleRelistingRepository

__all__ = [
    "ListingRepository",
    "ProfileRepository",
    "SnapshotRepository",
    "TrackingRunRepository",
    "TrackedProfileRepository",
    "TrackedListingRepository",
    "TrackedSearchRepository",
    "SearchMatchRepository",
    "TrackingEventRepository",
    "NotificationDeliveryRepository",
    "PossibleRelistingRepository",
]
