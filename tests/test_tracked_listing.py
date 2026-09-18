from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.domain.listing_urls import parse_listing_reference
from wallapop_tracker.domain.notifications import DeliveryResult
from wallapop_tracker.exceptions import WallapopNotFoundError, WallapopRateLimitError
from wallapop_tracker.models import Listing, Profile
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.services.listing_tracker import TrackedListingTracker
from wallapop_tracker.services.notifications import NotificationService
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    TrackedListingRecord,
    TrackingEventRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    ProfileRepository,
    TrackedListingRepository,
    TrackedSearchRepository,
    TrackingRunRepository,
)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def listing(
    price: str = "100",
    *,
    title: str = "Cámara",
    reserved: bool = False,
    shipping: bool = True,
    status: str = "active",
) -> Listing:
    return Listing(
        item_id="item-1",
        user_id="seller-1",
        title=title,
        price=Decimal(price),
        currency="EUR",
        reserved=reserved,
        shipping_available=shipping,
        status=status,
        url="https://es.wallapop.com/item/camara-1",
    )


def create_tracked_listing(database: Database) -> int:
    with database.transaction() as session:
        global_listing, _ = ListingRepository(session).get_or_create_global_listing(
            listing(), None
        )
        return TrackedListingRepository(session).create(global_listing.id, "camera").id


class FakeProvider:
    def __init__(self, *values: Listing | BaseException):
        self.values = list(values)

    async def get(self, item_id: str) -> Listing:
        assert item_id == "item-1"
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeSearchProvider:
    async def search(self, request: SearchRequest) -> list[Listing]:
        del request
        return [listing()]


@pytest.mark.asyncio
async def test_tracked_listing_runs_and_detects_price_title_and_flags(database):
    tracked_id = create_tracked_listing(database)
    provider = FakeProvider(
        listing(),
        listing("80", title="Cámara nueva", reserved=True, shipping=False),
    )
    tracker = TrackedListingTracker(provider, database)

    first = await tracker.track_listing(tracked_id)
    second = await tracker.track_listing(tracked_id)

    assert first.status == TrackingRunStatus.VALID
    assert {alert.type for alert in second.alerts} == {
        AlertType.PRICE_DROP,
        AlertType.TITLE_CHANGE,
        AlertType.RESERVATION_CHANGE,
        AlertType.SHIPPING_CHANGE,
    }
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TrackingRunRecord)) == 2
        assert session.scalar(select(func.count()).select_from(TrackingEventRecord)) == 4


@pytest.mark.asyncio
async def test_price_increase_status_change_and_idempotent_restart(database):
    tracked_id = create_tracked_listing(database)
    provider = FakeProvider(
        listing(), listing("120", status="paused"), listing("120", status="paused")
    )
    tracker = TrackedListingTracker(provider, database)

    await tracker.track_listing(tracked_id)
    changed = await tracker.track_listing(tracked_id)
    restarted = await tracker.track_listing(tracked_id)

    assert {alert.type for alert in changed.alerts} == {
        AlertType.PRICE_INCREASE,
        AlertType.STATUS_CHANGE,
    }
    assert restarted.alerts == ()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TrackingEventRecord)) == 2


@pytest.mark.asyncio
async def test_removed_and_reappeared_preserve_global_listing(database):
    tracked_id = create_tracked_listing(database)
    provider = FakeProvider(listing(), WallapopNotFoundError("missing"), listing("90"))
    tracker = TrackedListingTracker(provider, database)

    await tracker.track_listing(tracked_id)
    removed = await tracker.track_listing(tracked_id)
    reappeared = await tracker.track_listing(tracked_id)

    assert [alert.type for alert in removed.alerts] == [AlertType.REMOVED]
    assert [alert.type for alert in reappeared.alerts] == [AlertType.REAPPEARED]
    with database.session() as session:
        record = session.scalar(select(ListingRecord))
        assert record is not None
        assert session.scalar(select(func.count()).select_from(ListingRecord)) == 1
        snapshots = session.scalars(
            select(ListingSnapshotRecord).order_by(ListingSnapshotRecord.id)
        ).all()
        assert [snapshot.presence_state for snapshot in snapshots] == [
            PresenceState.ACTIVE,
            PresenceState.REMOVED,
            PresenceState.ACTIVE,
        ]


@pytest.mark.asyncio
async def test_transient_failure_does_not_mark_listing_removed(database):
    tracked_id = create_tracked_listing(database)
    provider = FakeProvider(listing(), WallapopRateLimitError("rate limited"))
    tracker = TrackedListingTracker(provider, database)

    await tracker.track_listing(tracked_id)
    failed = await tracker.track_listing(tracked_id)

    assert failed.status == TrackingRunStatus.FAILED
    with database.session() as session:
        assert session.scalar(
            select(func.count())
            .select_from(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.presence_state == PresenceState.REMOVED)
        ) == 0


@pytest.mark.asyncio
async def test_notification_delivery_for_listing_event(database):
    tracked_id = create_tracked_listing(database)
    provider = FakeProvider(listing(), listing("80"))
    tracker = TrackedListingTracker(provider, database)
    await tracker.track_listing(tracked_id)
    result = await tracker.track_listing(tracked_id)
    channel = FakeChannel()
    service = NotificationService(
        database, channels={"webhook": channel}, destinations={"webhook": ["destination"]}
    )

    service.enqueue_events(alert.event_id for alert in result.alerts)
    assert await service.deliver_pending() == 1


@pytest.mark.asyncio
async def test_search_discovered_listing_is_reused_by_tracked_listing(database):
    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("camera").id
    await SearchTracker(FakeSearchProvider(), database).track_search(search_id)
    with database.transaction() as session:
        global_listing = session.scalar(select(ListingRecord))
        assert global_listing is not None
        tracked_id = TrackedListingRepository(session).create(
            global_listing.id, "from-search"
        ).id
    result = await TrackedListingTracker(FakeProvider(listing()), database).track_listing(
        tracked_id
    )
    assert result.status == TrackingRunStatus.VALID
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ListingRecord)) == 1


def test_profile_discovered_listing_is_reused_by_tracked_listing(database):
    with database.transaction() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="seller-1"), observed_at=datetime.now(UTC)
        )
        run = TrackingRunRepository(session).start_profile_run(profile.id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        global_listing = ListingRepository(session).get_or_create_listing(
            listing(), profile.id, tracking_run_id=run.id
        )
        tracked_id = TrackedListingRepository(session).create(
            global_listing.id, "from-profile"
        ).id
    with database.session() as session:
        tracked = session.get(TrackedListingRecord, tracked_id)
        assert tracked is not None
        assert tracked.listing_id == global_listing.id


class FakeChannel:
    async def send(self, notification, destination):
        del notification, destination
        return DeliveryResult(True)


def test_tracked_listing_alias_and_url_constraints(database):
    tracked_id = create_tracked_listing(database)
    with database.transaction() as session:
        tracked = session.get(TrackedListingRecord, tracked_id)
        assert tracked is not None
        with session.begin_nested():
            with pytest.raises(IntegrityError):
                session.add(
                    TrackedListingRecord(
                        listing_id=tracked.listing_id,
                        alias="other",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
                session.flush()
    assert parse_listing_reference("https://es.wallapop.com/item/camara-1181437490") == "1181437490"
    assert parse_listing_reference("item-1") == "item-1"
    with pytest.raises(ValueError):
        parse_listing_reference("https://example.com/item/camara-1")
