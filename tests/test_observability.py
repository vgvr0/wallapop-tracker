import json
import logging
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import text

from wallapop_tracker.api import create_app
from wallapop_tracker.client import WallapopClient
from wallapop_tracker.observability import Metrics, configure_logging, get_metrics, log_event
from wallapop_tracker.storage.database import Database


def test_health_does_not_require_database_and_metrics_use_route_templates():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    metrics = Metrics()
    app = create_app(database, metrics=metrics)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/searches").status_code == 200
        exposition = client.get("/metrics").text
    assert "http_requests_total" in exposition
    assert "/api/v1/searches" in exposition
    database.close()


def test_health_stays_alive_when_database_connection_is_gone():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    app = create_app(database, metrics=Metrics())
    database.engine.dispose()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
    database.close()


def test_ready_requires_current_alembic_revision():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    app = create_app(database, metrics=Metrics())
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 503
        with database.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES ('0013_marketplace_identity')")
            )
        assert client.get("/ready").status_code == 200
    database.close()


def test_json_logging_redacts_secret(monkeypatch, caplog):
    monkeypatch.setenv("WALLAPOP_LOG_FORMAT", "json")
    configure_logging()
    caplog.set_level(logging.INFO)
    log_event(
        logging.getLogger("test-observability"),
        logging.INFO,
        "delivery",
        destination="https://secret.example/token",
    )
    handler = next(
        item for item in logging.getLogger().handlers if getattr(item, "_wallapop", False)
    )
    output = handler.format(caplog.records[-1])
    record = json.loads(output)
    assert record["event"] == "delivery"
    assert "secret.example" not in output


@pytest.mark.asyncio
@respx.mock
async def test_wallapop_429_and_retry_metrics():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "stats.json").read_text(encoding="utf-8")
    )
    route = respx.get("https://api.wallapop.com/api/v3/users/user-1/stats").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=fixture)]
    )
    metrics = get_metrics()
    before_429 = metrics.wallapop_http_429_total.labels("request")._value.get()
    before_retry = metrics.wallapop_http_retries_total.labels("request")._value.get()
    async with WallapopClient(min_interval=0, backoff_factor=0) as client:
        await client.get_profile_stats("user-1")
    assert route.called
    assert metrics.wallapop_http_429_total.labels("request")._value.get() > before_429
    assert metrics.wallapop_http_retries_total.labels("request")._value.get() > before_retry
