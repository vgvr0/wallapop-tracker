from decimal import Decimal

import pytest
from sqlalchemy import func, select

from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.services.tracker import ProfileTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    ProfileRecord,
    ProfileSnapshotRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
)
from wallapop_tracker.storage.repositories import TrackedSearchRepository


@pytest.fixture
def database():
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.close()


class FakeClient:
    def __init__(
        self,
        listings: list[Listing],
        *,
        items_error: Exception | None = None,
        profile_error: Exception | None = None,
    ):
        self.listings = listings
        self.items_error = items_error
        self.profile_error = profile_error

    async def resolve_user_id(self, url: str) -> str:
        return "user-1"

    async def get_profile(self, user_id: str) -> Profile:
        if self.profile_error:
            raise self.profile_error
        return Profile(user_id=user_id, name="Ana", url="https://wallapop.test/user/ana")

    async def get_profile_stats(self, user_id: str) -> ProfileStats:
        return ProfileStats(
            rating=4.5,
            review_count=12,
            published_count=len(self.listings),
            purchases_count=3,
            sales_count=4,
            sold_count=2,
            reports_count=1,
        )

    async def get_review_summary(self, user_id: str) -> ReviewSummary:
        return ReviewSummary(rating=4.5, review_count=12, rating_distribution={5: 80, 4: 20})

    async def get_all_items(self, user_id: str) -> list[Listing]:
        if self.items_error:
            raise self.items_error
        return self.listings


class FakeSearchProvider:
    def __init__(self, listings: list[Listing]):
        self.listings = listings

    async def search(self, request: SearchRequest) -> list[Listing]:
        return self.listings


def item(item_id: str, price: str) -> Listing:
    return Listing(
        item_id=item_id,
        user_id="user-1",
        title=item_id,
        price=Decimal(price),
        currency="EUR",
        status="active",
    )


@pytest.mark.asyncio
async def test_valid_capture_and_identical_second_capture(database):
    listings = [item("a", "120"), item("b", "50")]
    tracker = ProfileTracker(FakeClient(listings), database)
    first = await tracker.track_profile("https://wallapop.test/user/ana")
    second = await tracker.track_profile("https://wallapop.test/user/ana")
    assert first.status.value == "valid"
    assert second.status.value == "valid"
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TrackingRunListingRecord)) == 4
        assert session.scalar(select(func.count()).select_from(ProfileSnapshotRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ListingSnapshotRecord)) == 2
        run = session.scalar(select(TrackingRunRecord).order_by(TrackingRunRecord.id))
        assert run is not None
        assert run.profile_id == session.scalar(select(ProfileRecord.id))
        assert run.tracked_search_id is None


@pytest.mark.asyncio
async def test_profile_tracking_enriches_listing_discovered_by_search(database):
    search_listing = item("shared", "120")
    with database.transaction() as session:
        search_id = TrackedSearchRepository(session).create("shared").id

    await SearchTracker(FakeSearchProvider([search_listing]), database).track_search(search_id)
    with database.session() as session:
        before = session.scalar(select(ListingSnapshotRecord.listing_id))
        assert before is not None
        assert session.get(TrackingRunRecord, 1).profile_id is None

    tracker = ProfileTracker(FakeClient([item("shared", "120")]), database)
    await tracker.track_profile("https://wallapop.test/user/ana")

    with database.session() as session:
        listings = session.scalars(select(ListingRecord)).all()
        assert len(listings) == 1
        assert listings[0].wallapop_item_id == "shared"
        assert listings[0].profile_id is not None


@pytest.mark.asyncio
async def test_price_change_removed_and_reappeared(database):
    tracker = ProfileTracker(FakeClient([item("a", "120"), item("b", "50")]), database)
    await tracker.track_profile("https://wallapop.test/user/ana")
    tracker.client = FakeClient([item("a", "100"), item("c", "80")])
    await tracker.track_profile("https://wallapop.test/user/ana")
    tracker.client = FakeClient([item("a", "100"), item("b", "50"), item("c", "80")])
    await tracker.track_profile("https://wallapop.test/user/ana")
    with database.session() as session:
        snapshots = session.scalars(
            select(ListingSnapshotRecord).order_by(ListingSnapshotRecord.id)
        ).all()
        assert [snapshot.presence_state for snapshot in snapshots if snapshot.listing_id == 2] == [
            PresenceState.ACTIVE,
            PresenceState.REMOVED,
            PresenceState.ACTIVE,
        ]
        assert len([snapshot for snapshot in snapshots if snapshot.listing_id == 1]) == 2


@pytest.mark.asyncio
async def test_partial_does_not_change_presence_or_last_seen(database):
    tracker = ProfileTracker(FakeClient([item("a", "120")]), database)
    await tracker.track_profile("https://wallapop.test/user/ana")
    tracker.client = FakeClient([], items_error=RuntimeError("page 2"))
    result = await tracker.track_profile("https://wallapop.test/user/ana")
    assert result.status.value == "partial"
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TrackingRunListingRecord)) == 1
        runs = session.scalars(select(TrackingRunRecord).order_by(TrackingRunRecord.id)).all()
        assert runs[-1].items_ok is False


@pytest.mark.asyncio
async def test_failed_capture_does_not_change_presence(database):
    tracker = ProfileTracker(FakeClient([item("a", "120")]), database)
    await tracker.track_profile("https://wallapop.test/user/ana")
    tracker.client = FakeClient([], profile_error=RuntimeError("HTTP 500"))
    result = await tracker.track_profile("https://wallapop.test/user/ana")
    assert result.status.value == "failed"
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TrackingRunListingRecord)) == 1


@pytest.mark.asyncio
async def test_profile_snapshot_uses_review_distribution(database):
    tracker = ProfileTracker(FakeClient([item("a", "120")]), database)
    await tracker.track_profile("https://wallapop.test/user/ana")
    with database.session() as session:
        snapshot = session.scalar(select(ProfileSnapshotRecord))
        assert snapshot is not None
        assert snapshot.rating_5_pct == 80
        assert snapshot.rating_4_pct == 20
        assert snapshot.review_count == 12
        assert snapshot.purchases_count == 3
        assert snapshot.sales_count == 4
        assert snapshot.sold_count == 2
        assert snapshot.reports_count == 1


@pytest.mark.asyncio
async def test_valid_capture_is_atomic_on_storage_failure(database, monkeypatch):
    tracker = ProfileTracker(FakeClient([item("a", "120")]), database)
    original = __import__(
        "wallapop_tracker.storage.repositories", fromlist=["SnapshotRepository"]
    ).SnapshotRepository.save_listing_snapshot

    def fail(*args, **kwargs):
        raise RuntimeError("storage failure")

    monkeypatch.setattr(
        __import__(
            "wallapop_tracker.storage.repositories", fromlist=["SnapshotRepository"]
        ).SnapshotRepository,
        "save_listing_snapshot",
        fail,
    )
    result = await tracker.track_profile("https://wallapop.test/user/ana")
    assert result.status.value == "failed"
    monkeypatch.setattr(
        __import__(
            "wallapop_tracker.storage.repositories", fromlist=["SnapshotRepository"]
        ).SnapshotRepository,
        "save_listing_snapshot",
        original,
    )
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ListingSnapshotRecord)) == 0
        assert session.scalar(select(func.count()).select_from(TrackingRunRecord)) == 1
