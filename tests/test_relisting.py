from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.domain.notifications import DeliveryResult
from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.services.notifications import NotificationService
from wallapop_tracker.services.relisting import RelistingDetectionService
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.services.tracker import ProfileTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PossibleRelistingRecord,
    PresenceState,
    TrackingEventRecord,
)
from wallapop_tracker.storage.repositories import (
    TrackedSearchRepository,
    TrackingRunRepository,
)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def item(item_id: str, title: str, price: str, seller: str = "seller-1") -> Listing:
    return Listing(
        item_id=item_id,
        user_id=seller,
        title=title,
        description="producto",
        price=Decimal(price),
        currency="EUR",
        category_id="phones",
        brand="Apple",
    )


def setup_pair(database: Database, *, old_at: datetime | None = None):
    observed = datetime.now(UTC)
    old_at = old_at or observed - timedelta(days=2)
    suffix = "-old" if observed - old_at > timedelta(days=30) else ""
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create("iphone", notify_on_first_run=True)
        runs = TrackingRunRepository(session)
        old_run = runs.start_search_run(search.id, started_at=old_at)
        runs.mark_valid(old_run.id, items_fetched=1, items_ok=True)
        previous = ListingRecord(
            wallapop_item_id=f"old-id{suffix}",
            seller_user_id="seller-1",
            first_seen_at=old_at,
            last_seen_at=old_at,
            created_at=old_at,
            updated_at=old_at,
        )
        current = ListingRecord(
            wallapop_item_id=f"new-id{suffix}",
            seller_user_id="seller-1",
            first_seen_at=observed,
            last_seen_at=observed,
            created_at=observed,
            updated_at=observed,
        )
        session.add_all([previous, current])
        session.flush()
        session.add(
            ListingSnapshotRecord(
                listing_id=previous.id,
                tracking_run_id=old_run.id,
                observed_at=old_at,
                title="iPhone 15 Pro 256GB",
                price=Decimal("850"),
                category_id="phones",
                brand="Apple",
                presence_state=PresenceState.REMOVED,
            )
        )
        current_run = runs.start_search_run(search.id, started_at=observed)
        runs.mark_valid(current_run.id, items_fetched=1, items_ok=True)
        return observed, previous.id, current.id, current_run.id


def detect(database: Database, current_id: int, run_id: int, listing: Listing, observed: datetime):
    with database.transaction() as session:
        current = session.get(ListingRecord, current_id)
        assert current is not None
        return RelistingDetectionService(session).detect_new_listing(
            current, listing, tracking_run_id=run_id, detected_at=observed
        )


def test_strong_match_is_explainable_and_does_not_merge(database):
    observed, previous_id, current_id, run_id = setup_pair(database)
    result = detect(
        database,
        current_id,
        run_id,
        item("new-id", "iPhone 15 Pro 256 GB", "840"),
        observed,
    )

    assert result is not None
    assert result.candidate.previous_listing_id == previous_id
    assert result.candidate.current_listing_id == current_id
    assert result.candidate.score >= 0.75
    assert {reason.name for reason in result.candidate.reasons} >= {
        "same_seller",
        "title_similarity",
        "price_delta",
    }
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(PossibleRelistingRecord)) == 1
        previous = session.get(ListingRecord, previous_id)
        current = session.get(ListingRecord, current_id)
        assert previous is not None and current is not None and previous.id != current.id
        event = session.get(TrackingEventRecord, result.event_id)
        assert event is not None
        assert event.event_type == AlertType.POSSIBLE_RELISTING.value


@pytest.mark.parametrize(
    ("title", "price"),
    [("iPhone 13 Mini", "840"), ("iPhone 15 Pro 256 GB", "200")],
)
def test_dissimilar_product_or_price_is_below_threshold(database, title, price):
    observed, _, current_id, run_id = setup_pair(database)
    assert detect(database, current_id, run_id, item("new-id", title, price), observed) is None


def test_missing_seller_does_not_break_detection(database):
    observed, _, current_id, run_id = setup_pair(database)
    with database.transaction() as session:
        current = session.get(ListingRecord, current_id)
        assert current is not None
        current.seller_user_id = None
    assert (
        detect(database, current_id, run_id, item("new-id", "iPhone 15 Pro", "840"), observed)
        is None
    )


def test_candidate_window_and_duplicate_restart(database):
    observed, _, current_id, run_id = setup_pair(
        database, old_at=datetime.now(UTC) - timedelta(days=31)
    )
    listing = item("new-id", "iPhone 15 Pro 256 GB", "840")
    assert detect(database, current_id, run_id, listing, observed) is None

    observed, previous_id, current_id, run_id = setup_pair(database)
    first = detect(database, current_id, run_id, listing, observed)
    second = detect(database, current_id, run_id, listing, observed)
    assert first is not None and second is not None
    assert first.record.id == second.record.id
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(PossibleRelistingRecord)) == 1


def test_relisting_does_not_cross_marketplaces(database):
    observed, previous_id, current_id, run_id = setup_pair(database)
    with database.transaction() as session:
        previous = session.get(ListingRecord, previous_id)
        current = session.get(ListingRecord, current_id)
        assert previous is not None and current is not None
        previous.marketplace = "vinted"
        result = RelistingDetectionService(session).detect_new_listing(
            current,
            item("new-id", "iPhone 15 Pro 256 GB", "840"),
            tracking_run_id=run_id,
            detected_at=observed,
        )
        assert result is None


@pytest.mark.asyncio
async def test_notification_delivery_for_possible_relisting(database):
    observed, _, current_id, run_id = setup_pair(database)
    result = detect(
        database,
        current_id,
        run_id,
        item("new-id", "iPhone 15 Pro 256 GB", "840"),
        observed,
    )
    assert result is not None

    class FakeChannel:
        async def send(self, notification, destination):
            del destination
            assert notification.event_type == AlertType.POSSIBLE_RELISTING
            assert notification.details is not None
            return DeliveryResult(True)

    service = NotificationService(
        database, channels={"webhook": FakeChannel()}, destinations={"webhook": ["dest"]}
    )
    service.enqueue_event(result.event_id)
    assert await service.deliver_pending() == 1


@pytest.mark.asyncio
async def test_cross_origin_search_then_profile_uses_same_global_listing_store(database):
    class SearchProvider:
        async def search(self, request: SearchRequest) -> list[Listing]:
            del request
            return [item("search-old", "iPhone 15 Pro 256GB", "850")]

    class ProfileClient:
        async def resolve_user_id(self, profile_url: str) -> str:
            del profile_url
            return "seller-1"

        async def get_profile(self, user_id: str) -> Profile:
            return Profile(user_id=user_id, url="https://es.wallapop.com/user/seller-1")

        async def get_profile_stats(self, user_id: str) -> ProfileStats:
            del user_id
            return ProfileStats()

        async def get_review_summary(self, user_id: str) -> ReviewSummary:
            del user_id
            return ReviewSummary()

        async def get_all_items(self, user_id: str) -> list[Listing]:
            del user_id
            return [item("profile-new", "iPhone 15 Pro 256 GB", "840")]

    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("iphone", notify_on_first_run=True).id
    await SearchTracker(SearchProvider(), database).track_search(search_id)
    old_at = datetime.now(UTC) - timedelta(days=1)
    with database.transaction() as session:
        old = session.scalar(
            select(ListingRecord).where(ListingRecord.wallapop_item_id == "search-old")
        )
        assert old is not None
        old.last_seen_at = old_at
        snapshot = session.scalar(
            select(ListingSnapshotRecord).where(ListingSnapshotRecord.listing_id == old.id)
        )
        assert snapshot is not None
        snapshot.observed_at = old_at
        snapshot.presence_state = PresenceState.REMOVED

    result = await ProfileTracker(ProfileClient(), database).track_profile(
        "https://es.wallapop.com/user/seller-1"
    )
    assert result.alerts
    assert result.alerts[0].type == AlertType.POSSIBLE_RELISTING
    with database.session() as session:
        listings = session.scalars(select(ListingRecord)).all()
        assert {listing.wallapop_item_id for listing in listings} == {
            "search-old",
            "profile-new",
        }
