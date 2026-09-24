import pytest

from wallapop_tracker.storage.models import TrackedSearchRecord
from wallapop_tracker.storage.repositories import TrackedSearchRepository


def test_explicit_location_is_persisted_and_validated(database):
    with database.transaction() as session:
        row = TrackedSearchRepository(session).create(
            "iphone", latitude=40.4168, longitude=-3.7038, max_distance_km=20
        )
        search_id = row.id

    with database.session() as session:
        stored = session.get(TrackedSearchRecord, search_id)
        assert stored is not None
        assert (stored.latitude, stored.longitude, stored.max_distance_km) == (
            40.4168,
            -3.7038,
            20.0,
        )

    invalid = (
        {"latitude": 40, "max_distance_km": 20},
        {"longitude": -3, "max_distance_km": 20},
        {"latitude": 100, "longitude": 0, "max_distance_km": 20},
        {"latitude": 40, "longitude": 200, "max_distance_km": 20},
        {"latitude": 40, "longitude": 0, "max_distance_km": 0},
        {"latitude": 40, "longitude": 0, "max_distance_km": -1},
        {"max_distance_km": 20},
    )
    for values in invalid:
        with pytest.raises(ValueError):
            with database.transaction() as session:
                TrackedSearchRepository(session).create("invalid", **values)
