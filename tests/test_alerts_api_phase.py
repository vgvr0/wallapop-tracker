from fastapi.testclient import TestClient

from wallapop_tracker.api import create_app
from wallapop_tracker.storage.database import Database


def test_alert_rules_crud_and_delivery_filters():
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    with TestClient(create_app(db)) as client:
        assert client.get("/api/v1/alerts/rules").json() == []
        assert client.get("/api/v1/alerts/deliveries").json() == []
        bad = client.post("/api/v1/alerts/rules", json={
            "event_type": "listing_sold", "channel": "discord", "destination": "x"
        })
        assert bad.status_code == 422
        created = client.post("/api/v1/alerts/rules", json={
            "event_type": "listing_sold", "channel": "webhook",
            "destination": "https://example.test", "filters": {"listing_id": 1}
        })
        assert created.status_code == 201
        rule_id = created.json()["id"]
        assert client.get(f"/api/v1/alerts/rules/{rule_id}").status_code == 200
        patched = client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"enabled": False})
        assert patched.status_code == 200 and patched.json()["enabled"] is False
        assert client.get("/api/v1/alerts/rules").json()[0]["id"] == rule_id
        assert client.delete(f"/api/v1/alerts/rules/{rule_id}").status_code == 204
        assert client.get("/api/v1/alerts/rules").json() == []
    db.close()

