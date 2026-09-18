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
