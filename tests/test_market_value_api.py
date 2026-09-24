import pytest
from fastapi.testclient import TestClient

from wallapop_tracker.api import create_app
from wallapop_tracker.storage.database import Database


@pytest.fixture
def api_client():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    with TestClient(create_app(database)) as client:
        yield client
    database.close()


def test_market_value_api_missing_listing(api_client):
    response = api_client.get("/api/v1/listings/999999/market-value")
    assert response.status_code == 404


def test_market_value_api_rejects_invalid_window(api_client):
    response = api_client.get("/api/v1/listings/999999/market-value?window_days=0")
    assert response.status_code == 422
