from decimal import Decimal

import pytest
from sqlalchemy import func, select

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.models import Listing
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    SearchListingMatchRecord,
    TrackedSearchRecord,
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


class SequenceProvider:
    def __init__(self, *batches: list[Listing]):
        self.batches = list(batches)

    async def search(self, request: SearchRequest) -> list[Listing]:
        return self.batches.pop(0)


class FailingProvider:
    async def search(self, request: SearchRequest) -> list[Listing]:
        raise RuntimeError("initial failure")


def listing(item_id: str, price: str = "100") -> Listing:
    return Listing(
        item_id=item_id,
        user_id="seller",
        title=item_id,
        price=Decimal(price),
        currency="EUR",
    )


def create_search(database: Database, *, notify_on_first_run: bool = False) -> int:
    with database.transaction() as session:
        return TrackedSearchRepository(session).create(
            "phone", notify_on_first_run=notify_on_first_run
        ).id


@pytest.mark.asyncio
async def test_default_initial_baseline_persists_without_new_events_or_deliveries(database):
    search_id = create_search(database)
    result = await SearchTracker(
        SequenceProvider([listing("A"), listing("B"), listing("C")]), database
    ).track_search(search_id)

    assert result.status == TrackingRunStatus.VALID
    assert result.matched_listings == 3
    assert result.new_listings == 0
    assert result.alerts == ()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ListingRecord)) == 3
        assert session.scalar(select(func.count()).select_from(ListingSnapshotRecord)) == 3
        assert session.scalar(select(func.count()).select_from(SearchListingMatchRecord)) == 3
        assert session.scalar(select(func.count()).select_from(TrackingEventRecord)) == 0


@pytest.mark.asyncio
async def test_second_run_only_emits_event_for_new_match(database):
    search_id = create_search(database)
    tracker = SearchTracker(
        SequenceProvider(
            [listing("A"), listing("B"), listing("C")],
            [listing("A"), listing("B"), listing("C"), listing("D")],
        ),
        database,
    )

    await tracker.track_search(search_id)
    result = await tracker.track_search(search_id)

    assert result.new_listings == 1
    assert [alert.listing_id for alert in result.alerts] == ["D"]


@pytest.mark.asyncio
async def test_failed_first_run_does_not_establish_baseline(database):
    search_id = create_search(database)
    failed = await SearchTracker(FailingProvider(), database).track_search(search_id)
    valid = await SearchTracker(
        SequenceProvider([listing("A"), listing("B")]), database
    ).track_search(search_id)

    assert failed.status == TrackingRunStatus.FAILED
    assert valid.status == TrackingRunStatus.VALID
    assert valid.new_listings == 0
    with database.session() as session:
        runs = session.scalars(select(TrackingRunRecord).order_by(TrackingRunRecord.id)).all()
        assert [run.status for run in runs] == [TrackingRunStatus.FAILED, TrackingRunStatus.VALID]


@pytest.mark.asyncio
async def test_notify_on_first_run_preserves_legacy_events(database):
    search_id = create_search(database, notify_on_first_run=True)
    result = await SearchTracker(
        SequenceProvider([listing("A"), listing("B"), listing("C")]), database
    ).track_search(search_id)

    assert result.new_listings == 3
    assert [alert.type for alert in result.alerts] == [AlertType.NEW_LISTING] * 3


@pytest.mark.asyncio
async def test_overlapping_silent_baseline_does_not_duplicate_global_event(database):
    first = create_search(database, notify_on_first_run=True)
    second = create_search(database)
    await SearchTracker(SequenceProvider([listing("ABC")]), database).track_search(first)
    result = await SearchTracker(SequenceProvider([listing("ABC")]), database).track_search(second)

    assert result.new_listings == 0
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(SearchListingMatchRecord)) == 2
        assert session.scalar(select(func.count()).select_from(TrackingEventRecord)) == 1


@pytest.mark.asyncio
async def test_silent_baseline_preserves_global_price_drop_from_history(database):
    first = create_search(database, notify_on_first_run=True)
    second = create_search(database)
    await SearchTracker(SequenceProvider([listing("ABC", "100")]), database).track_search(first)
    result = await SearchTracker(
        SequenceProvider([listing("ABC", "80")]), database
    ).track_search(second)

    assert result.new_listings == 0
    assert result.price_changes == 1
    assert [alert.type for alert in result.alerts] == [AlertType.PRICE_DROP]


@pytest.mark.asyncio
async def test_baseline_state_survives_tracker_restart(database):
    search_id = create_search(database)
    await SearchTracker(SequenceProvider([listing("A")]), database).track_search(search_id)
    restarted = SearchTracker(SequenceProvider([listing("A")]), database)
    result = await restarted.track_search(search_id)

    assert result.new_listings == 0
    with database.session() as session:
        search = session.get(TrackedSearchRecord, search_id)
        assert search is not None and search.last_run_status == TrackingRunStatus.VALID.value
