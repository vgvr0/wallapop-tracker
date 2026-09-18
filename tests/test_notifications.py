import json
from datetime import UTC, datetime
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
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    TrackingEventRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import TrackedSearchRepository


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
        return TrackedSearchRepository(session).create(
            "cámara", notify_on_first_run=True
        ).id


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
        assert session.scalar(select(NotificationDeliveryRecord).where(
            NotificationDeliveryRecord.id == first[0].id
        )).status == NotificationDeliveryStatus.PENDING

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
