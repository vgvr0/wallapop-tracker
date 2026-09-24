import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.domain.notifications import DeliveryResult, Notification
from wallapop_tracker.models import Listing
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.services.notifications import (
    DiscordWebhookChannel,
    NotificationService,
    TelegramNotificationChannel,
    WebhookNotificationChannel,
)
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    TrackedSearchRecord,
    TrackingEventRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import (
    NotificationDeliveryRepository,
    TrackedSearchRepository,
)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


class FakeChannel:
    def __init__(self, *results: DeliveryResult):
        self.results = list(results)
        self.notifications: list[Notification] = []

    async def send(self, notification: Notification, destination: str) -> DeliveryResult:
        del destination
        self.notifications.append(notification)
        return self.results.pop(0) if self.results else DeliveryResult(True)


class SearchProvider:
    async def search(self, request: SearchRequest) -> list[Listing]:
        return [
            Listing(
                item_id="item-ñ",
                user_id="seller-1",
                title="Cámara 🚲",
                description=None,
                price=Decimal("80.00"),
                currency="EUR",
                url="https://es.wallapop.com/item/item-n",
            )
        ]


def create_search(database: Database) -> int:
    with database.transaction() as session:
        return TrackedSearchRepository(session).create("cámara", notify_on_first_run=True).id


async def create_event(database: Database) -> int:
    search_id = create_search(database)
    result = await SearchTracker(SearchProvider(), database).track_search(search_id)
    assert result.status == TrackingRunStatus.VALID
    assert len(result.alerts) == 1
    return result.alerts[0].event_id


@pytest.mark.asyncio
async def test_delivery_is_persisted_once_and_fk_is_enforced(database):
    event_id = await create_event(database)
    channel = FakeChannel()
    service = NotificationService(
        database, channels={"webhook": channel}, destinations={"webhook": ["dest"]}
    )

    first = service.enqueue_event(event_id)
    second = service.enqueue_event(event_id)

    assert first[0].id == second[0].id
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1
        assert (
            session.scalar(
                select(NotificationDeliveryRecord).where(
                    NotificationDeliveryRecord.id == first[0].id
                )
            ).status
            == NotificationDeliveryStatus.PENDING
        )

    with pytest.raises(IntegrityError):
        with database.transaction() as session:
            session.add(
                NotificationDeliveryRecord(
                    event_id=99999,
                    channel="webhook",
                    destination="other",
                    status=NotificationDeliveryStatus.PENDING,
                    attempts=0,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )


@pytest.mark.asyncio
async def test_successful_delivery_records_attempt_and_timestamp(database):
    event_id = await create_event(database)
    channel = FakeChannel()
    service = NotificationService(
        database, channels={"webhook": channel}, destinations={"webhook": ["dest"]}
    )
    service.enqueue_event(event_id)

    assert await service.deliver_pending() == 1
    with database.session() as session:
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.status == NotificationDeliveryStatus.DELIVERED
        assert delivery.attempts == 1
        assert delivery.delivered_at is not None
        assert delivery.last_error is None
    assert channel.notifications[0].new_price == Decimal("80.00")
    assert channel.notifications[0].title == "Cámara 🚲"


@pytest.mark.asyncio
async def test_failed_delivery_can_be_retried_without_new_row(database):
    event_id = await create_event(database)
    channel = FakeChannel(
        DeliveryResult(False, retryable=True, error="timeout"), DeliveryResult(True)
    )
    service = NotificationService(
        database, channels={"webhook": channel}, destinations={"webhook": ["dest"]}
    )
    service.enqueue_event(event_id)

    assert await service.deliver_pending() == 0
    with database.session() as session:
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.status == NotificationDeliveryStatus.FAILED
        assert delivery.attempts == 1
        assert delivery.last_error == "timeout"

    assert await service.retry_failed() == 1
    with database.session() as session:
        delivery = session.scalar(select(NotificationDeliveryRecord))
        assert delivery is not None
        assert delivery.status == NotificationDeliveryStatus.DELIVERED
        assert delivery.attempts == 2
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1


@pytest.mark.asyncio
async def test_failed_delivery_respects_backoff_when_include_failed_is_true(database):
    event_id = await create_event(database)
    now = datetime.now(UTC)
    with database.transaction() as session:
        delivery = NotificationDeliveryRepository(session).create_once(
            event_id=event_id,
            channel="webhook",
            destination="dest",
            created_at=now,
        )[0]
        delivery.status = NotificationDeliveryStatus.FAILED
        delivery.next_attempt_at = now + timedelta(minutes=5)

    with database.transaction() as session:
        assert (
            NotificationDeliveryRepository(session).claim_next(
                now=now,
                worker_id="early-retry",
                lease_seconds=60,
                max_attempts=3,
                include_failed=True,
            )
            is None
        )


@pytest.mark.asyncio
async def test_channels_are_independent_and_tracking_remains_valid(database):
    event_id = await create_event(database)
    telegram = FakeChannel(DeliveryResult(False, error="unauthorized"))
    discord = FakeChannel(DeliveryResult(True))
    service = NotificationService(
        database,
        channels={"telegram": telegram, "discord": discord},
        destinations={"telegram": ["chat-1"], "discord": ["webhook-1"]},
    )
    service.enqueue_event(event_id)

    assert await service.deliver_pending() == 1
    with database.session() as session:
        deliveries = session.scalars(
            select(NotificationDeliveryRecord).order_by(NotificationDeliveryRecord.channel)
        ).all()
        assert [delivery.status for delivery in deliveries] == [
            NotificationDeliveryStatus.DELIVERED,
            NotificationDeliveryStatus.FAILED,
        ]
        event = session.get(TrackingEventRecord, event_id)
        assert event is not None
        run = session.get(TrackingRunRecord, event.tracking_run_id)
        assert run is not None
        assert run.status == TrackingRunStatus.VALID


@pytest.mark.asyncio
async def test_new_service_resumes_existing_pending_delivery(database):
    event_id = await create_event(database)
    first_channel = FakeChannel(DeliveryResult(False, error="temporary"))
    first = NotificationService(
        database, channels={"webhook": first_channel}, destinations={"webhook": ["dest"]}
    )
    first.enqueue_event(event_id)
    await first.deliver_pending()

    second_channel = FakeChannel(DeliveryResult(True))
    second = NotificationService(
        database, channels={"webhook": second_channel}, destinations={"webhook": ["dest"]}
    )
    assert await second.retry_failed() == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(NotificationDeliveryRecord)) == 1


@pytest.mark.asyncio
async def test_http_channels_serialize_money_utc_and_unicode():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    notification = Notification(
        event_id=7,
        event_type=AlertType.PRICE_DROP,
        listing_id="item-ñ",
        title="Cámara 🚲",
        url=None,
        old_price=Decimal("100.00"),
        new_price=Decimal("80.00"),
        created_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await WebhookNotificationChannel(http_client=client).send(
            notification, "https://hook"
        )
        assert result.delivered
        await DiscordWebhookChannel(http_client=client).send(notification, "https://discord")
        await TelegramNotificationChannel("secret", http_client=client).send(notification, "123")

    payload = json.loads(requests[0].content)
    assert payload["old_price"] == "100.00"
    assert payload["new_price"] == "80.00"
    assert payload["created_at"] == "2026-01-02T03:04:00+00:00"
    assert payload["title"] == "Cámara 🚲"
    assert b"secret" not in requests[-1].content


def test_common_notification_payload_explains_advanced_alert():
    notification = Notification(
        event_id=8,
        event_type=AlertType.DEAL_SCORE_THRESHOLD,
        listing_id="item-1",
        title="Camera",
        url=None,
        old_price=Decimal("100"),
        new_price=Decimal("80"),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        details="Score 70 -> 85; threshold 80",
    )
    payload = WebhookNotificationChannel().payload(notification)
    assert payload["event_type"] == "DEAL_SCORE_THRESHOLD"
    assert payload["title"] == "Camera"
    assert payload["details"] == "Score 70 -> 85; threshold 80"


@pytest.mark.parametrize(
    "event_type",
    [
        AlertType.TARGET_PRICE_REACHED.value,
        AlertType.PERCENTAGE_DROP.value,
        AlertType.NEW_30D_LOW.value,
        AlertType.NEW_90D_LOW.value,
        AlertType.NEW_ALL_TIME_LOW.value,
        AlertType.DEAL_SCORE_THRESHOLD.value,
    ],
)
def test_advanced_event_formatter_includes_metadata(database, event_type):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with database.transaction() as session:
        listing = ListingRecord(
            wallapop_item_id="item-1",
            external_id="item-1",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(listing)
        session.flush()
        session.add(
            TrackingRunRecord(
                id=1,
                started_at=now,
                finished_at=now,
                tracked_search_id=3,
                status=TrackingRunStatus.VALID,
            )
        )
        session.add(TrackedSearchRecord(id=3, query="camera", created_at=now, updated_at=now))
        event = TrackingEventRecord(
            event_type=event_type,
            idempotency_key=event_type,
            listing_id=listing.id,
            tracking_run_id=1,
            tracked_search_id=3,
            old_price=Decimal("100"),
            new_price=Decimal("80"),
            created_at=now,
            metadata_json=json.dumps({"threshold": 80, "tracked_search_id": 3}),
        )
        session.add(event)
        session.flush()
        notification = NotificationService._notification(session, event)
    assert notification.event_type.value == event_type
    assert notification.details
    assert "threshold=80" in notification.details
