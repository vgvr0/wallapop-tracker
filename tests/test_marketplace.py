from datetime import UTC, datetime

from fastapi.testclient import TestClient

from wallapop_tracker.api.app import create_app
from wallapop_tracker.domain.marketplace import Marketplace
from wallapop_tracker.models import Listing
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import ListingRecord
from wallapop_tracker.storage.repositories import ListingRepository, TrackedSearchRepository


def test_listing_defaults_to_wallapop_and_search_exposes_marketplace():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    try:
        with database.transaction() as session:
            search = TrackedSearchRepository(session).create("phone")
            listing, _ = ListingRepository(session).get_or_create_global_listing(
                Listing(item_id="ABC", user_id="seller"), None, observed_at=datetime.now(UTC)
            )
            assert search.marketplace == Marketplace.WALLAPOP.value
            assert listing.marketplace == Marketplace.WALLAPOP.value
            assert listing.external_id == "ABC"
        with TestClient(create_app(database)) as client:
            response = client.post(
                "/api/v1/searches",
                json={"query": "phone", "marketplace": "vinted"},
            )
            assert response.status_code == 422
            assert "unsupported marketplace" in response.json()["detail"]
    finally:
        database.close()


def test_listing_identity_is_composite_for_future_marketplaces():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    try:
        with database.transaction() as session:
            now = datetime.now(UTC)
            first = ListingRepository(session).get_or_create_global_listing(
                Listing(item_id="ABC", user_id="seller"), None
            )[0]
            other = ListingRecord(
                marketplace="vinted",
                external_id="ABC",
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(other)
            session.flush()
            assert first.external_id == other.external_id == "ABC"
    finally:
        database.close()
