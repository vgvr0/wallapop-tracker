import pytest
from fastapi.testclient import TestClient

from wallapop_tracker.api import create_app
from wallapop_tracker.storage.database import Database


@pytest.fixture
def api_client():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    app = create_app(database)
    with TestClient(app) as client:
        client._health_database = database
        yield client
    database.close()


def test_health_and_search_crud(api_client):
    assert api_client.get("/health").json() == {"status": "ok"}
    created = api_client.post(
        "/api/v1/searches",
        json={"name": "Phones", "query": "iphone", "min_price": "100.00"},
    )
    assert created.status_code == 201
    assert created.json()["min_price"] == "100.00"
    search_id = created.json()["id"]
    assert api_client.get(f"/api/v1/searches/{search_id}").status_code == 200
    patched = api_client.patch(
        f"/api/v1/searches/{search_id}", json={"enabled": False, "interval_seconds": 900}
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False


def test_health_api_endpoints_work_without_data(api_client):
    assert api_client.get("/api/v1/health").status_code == 200
    assert api_client.get("/api/v1/health/runs").status_code == 200
    assert api_client.get("/api/v1/health/searches/999").status_code == 200
    assert api_client.get("/api/v1/health/profiles/999").status_code == 200


def test_health_api_endpoints_work_with_data(api_client):
    from datetime import UTC, datetime

    from wallapop_tracker.storage.models import TrackingRunRecord, TrackingRunStatus

    created = api_client.post("/api/v1/searches", json={"query": "phone"}).json()
    now = datetime.now(UTC)
    with api_client._health_database.transaction() as session:
        session.add(TrackingRunRecord(
            tracked_search_id=created["id"], started_at=now, finished_at=now,
            status=TrackingRunStatus.VALID, health_status="SUCCESS", duration_ms=100,
        ))
    assert api_client.get("/api/v1/health").json()["runs_24h"] == 1
    assert api_client.get("/api/v1/health/runs").json()[0]["status"] == "SUCCESS"
    assert api_client.get(f"/api/v1/health/searches/{created['id']}").json()["status"] == "HEALTHY"
    assert api_client.get("/api/v1/health/profiles/999").json()["status"] == "DEGRADED"


def test_search_import_and_pagination(api_client):
    imported = api_client.post(
        "/api/v1/searches/import",
        json={
            "url": "https://es.wallapop.com/app/search?keywords=iphone&minPrice=10",
            "interval_seconds": 3600,
        },
    )
    assert imported.status_code == 201
    assert imported.json()["query"] == "iphone"
    assert api_client.get("/api/v1/searches?limit=1&offset=1").status_code == 200
    invalid = api_client.post("/api/v1/searches/import", json={"url": "https://example.com"})
    assert invalid.status_code == 422


def test_errors_and_validation(api_client):
    assert api_client.get("/api/v1/searches/999").status_code == 404
    invalid = api_client.post("/api/v1/searches", json={"query": "", "interval_seconds": 0})
    assert invalid.status_code == 422
    assert api_client.get("/api/v1/listings?limit=201").status_code == 422


def test_advanced_alert_configuration_round_trips_for_search(api_client):
    created = api_client.post("/api/v1/searches", json={"query": "camera"}).json()
    assert created["target_price"] is None
    assert created["percentage_drop_threshold"] is None
    assert created["deal_score_threshold"] is None
    updated = api_client.patch(
        f"/api/v1/searches/{created['id']}",
        json={"target_price": "500", "percentage_drop_threshold": "10", "deal_score_threshold": "80",
              "notify_on_30d_low": True, "notify_on_90d_low": True, "notify_on_all_time_low": True},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["target_price"] == "500.00"
    assert body["percentage_drop_threshold"] == "10.00"
    assert body["deal_score_threshold"] == "80.00"
    assert body["notify_on_90d_low"] is True


def test_tracked_listing_advanced_alerts_patch_preserves_unmodified_fields(api_client):
    from decimal import Decimal

    from wallapop_tracker.models import Listing
    from wallapop_tracker.storage.repositories import ListingRepository

    with api_client._health_database.transaction() as session:
        listing, _ = ListingRepository(session).get_or_create_global_listing(
            Listing(item_id="item-1", user_id="seller", title="Camera", price=Decimal("100"),
                    currency="EUR", url="https://example/item-1"), None
        )
        listing_id = listing.id
    created = api_client.post("/api/v1/tracked-listings", json={
        "listing_id": listing_id, "alias": "camera", "target_price": "50",
        "percentage_drop_threshold": "10", "deal_score_threshold": "80",
        "notify_on_30d_low": True, "notify_on_90d_low": True,
    })
    assert created.status_code == 201
    tracked_id = created.json()["id"]
    patched = api_client.patch(f"/api/v1/tracked-listings/{tracked_id}",
                               json={"target_price": "40"})
    assert patched.status_code == 200
    body = patched.json()
    assert body["target_price"] in {"40", "40.00"}
    assert body["percentage_drop_threshold"] in {"10", "10.00"}
    assert body["deal_score_threshold"] in {"80", "80.00"}
    cleared = api_client.patch(f"/api/v1/tracked-listings/{tracked_id}",
                               json={"target_price": None, "deal_score_threshold": None})
    assert cleared.status_code == 200
    assert cleared.json()["target_price"] is None
    assert cleared.json()["deal_score_threshold"] is None
