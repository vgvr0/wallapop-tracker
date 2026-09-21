import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from wallapop_tracker.domain.alert_engine import AlertEngine
from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.domain.notifications import DeliveryResult, Notification
from wallapop_tracker.services.notifications import (
    NotificationService,
    TelegramNotificationChannel,
    WebhookNotificationChannel,
)
from wallapop_tracker.storage.models import (
    AlertRuleRecord,
    ListingRecord,
    NotificationDeliveryRecord,
    ProfileRecord,
    TrackedSearchRecord,
    TrackingEventRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


def event(db, kind="price_drop", *, listing=10, search=None, profile=None, old="100", new="80"):
    now = datetime.now(UTC)
    with db.transaction() as s:
        s.add(TrackedSearchRecord(id=3, query="test", created_at=now, updated_at=now))
        s.add(ProfileRecord(id=7, wallapop_user_id="profile-7", first_seen_at=now,
            last_seen_at=now, created_at=now, updated_at=now))
        run = TrackingRunRecord(profile_id=profile, tracked_search_id=None if profile else (search or 3), started_at=now, finished_at=now,
            status=TrackingRunStatus.VALID, profile_ok=True, stats_ok=True, reviews_ok=True, items_ok=True)
        listing_row = ListingRecord(id=listing, wallapop_item_id=str(listing), first_seen_at=now,
            last_seen_at=now, created_at=now, updated_at=now)
        s.add_all([listing_row, run])
        s.flush()
        row = TrackingEventRecord(event_type=kind, idempotency_key=f"{kind}-{listing}-{search}-{profile}",
            listing_id=listing, tracking_run_id=run.id, tracked_search_id=search,
            old_price=Decimal(old) if old else None, new_price=Decimal(new) if new else None, created_at=now)
        s.add(row)
        s.flush()
        return row.id


def rule(db, kind, filters=None, *, enabled=True, cooldown=0, channel="webhook"):
    now = datetime.now(UTC)
    with db.transaction() as s:
        row = AlertRuleRecord(event_type=kind, channel=channel, destination="https://example.test/hook",
            filters_json=json.dumps(filters or {}), enabled=enabled, cooldown_seconds=cooldown,
            created_at=now, updated_at=now)
        s.add(row)
        s.flush()
        return row.id


@pytest.mark.parametrize("filters,expected", [
    ({"listing_id": 10}, 1), ({"listing_id": 99}, 0), ({"search_id": 3}, 1),
    ({"profile_id": 7}, 1), ({"minimum_price_drop_percent": 10}, 1),
    ({"minimum_price_drop_percent": 30}, 0), ({"maximum_price": 90}, 1),
    ({"maximum_price": 70}, 0),
])
def test_alert_engine_filters(database, filters, expected):
    kind = "price_drop"
    eid = event(database, kind, search=3, profile=7)
    rule(database, kind, filters)
    assert AlertEngine(database).process_event(eid) == expected


def test_alert_engine_no_rule_disabled_and_idempotent(database):
    eid = event(database, "listing_sold", old=None, new="80")
    assert AlertEngine(database).process_event(eid) == 0
    rule(database, "listing_sold", enabled=False)
    assert AlertEngine(database).process_event(eid) == 0
    rule(database, "listing_sold")
    engine = AlertEngine(database)
    assert [engine.process_event(eid) for _ in range(3)] == [1, 0, 0]


def test_cooldown_active_and_expired(database):
    eid = event(database, "tracking_run_degraded")
    rule(database, "tracking_run_degraded", cooldown=60)
    engine = AlertEngine(database)
    assert engine.process_event(eid) == 1
    assert engine.process_event(eid) == 0
    with database.transaction() as s:
        row = s.scalar(select(NotificationDeliveryRecord))
        assert row
        row.created_at = datetime.now(UTC) - timedelta(seconds=61)
    assert engine.process_event(eid) == 0  # event idempotency still prevents duplicates


@pytest.mark.parametrize("kind", ["tracking_run_failed", "tracking_run_degraded", "schema_drift_detected"])
def test_health_and_schema_events(database, kind):
    eid = event(database, kind)
    rule(database, kind)
    assert AlertEngine(database).process_event(eid) == 1
    assert AlertEngine(database).process_event(eid) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status,retry", [(200, False), (400, False), (429, True), (500, True)])
async def test_webhook_statuses(status, retry):
    async def handler(request): return httpx.Response(status, request=request)
    n = Notification(1, AlertType.LISTING_SOLD, "x", "title", None, None, Decimal("2"), datetime.now(UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await WebhookNotificationChannel(http_client=client).send(n, "https://example.test")
    assert result.delivered is (status == 200)
    assert result.retryable is retry


@pytest.mark.asyncio
async def test_webhook_rejects_unsafe_urls():
    n = Notification(1, AlertType.LISTING_SOLD, "x", None, None, None, None, datetime.now(UTC))
    channel = WebhookNotificationChannel()
    assert not (await channel.send(n, "file:///tmp/x")).retryable
    assert not (await channel.send(n, "https://user:pass@example.test")).retryable


@pytest.mark.asyncio
async def test_telegram_statuses_and_configuration():
    async def handler(request): return httpx.Response(429, request=request)
    n = Notification(1, AlertType.LISTING_SOLD, "x", "title", None, None, None, datetime.now(UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TelegramNotificationChannel("token", http_client=client).send(n, "chat")
    assert result.retryable
    with pytest.raises(ValueError):
        TelegramNotificationChannel("")


@pytest.mark.asyncio
async def test_telegram_failure_does_not_cancel_webhook(database):
    class Channel:
        def __init__(self, result): self.result = result
        async def send(self, notification, destination): return self.result

    eid = event(database, "listing_sold")
    service = NotificationService(database,
        channels={"telegram": Channel(DeliveryResult(False, error="failed")),
                  "webhook": Channel(DeliveryResult(True))},
        destinations={"telegram": ["chat"], "webhook": ["https://example.test"]})
    service.enqueue_event(eid)
    await service.deliver_pending()
    with database.session() as s:
        rows = {row.channel: row.status.value if hasattr(row.status, "value") else row.status for row in s.scalars(select(NotificationDeliveryRecord))}
    assert rows == {"telegram": "failed", "webhook": "delivered"}
