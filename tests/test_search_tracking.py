from decimal import Decimal

import pytest
from sqlalchemy import func, select

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.models import Listing
from wallapop_tracker.reporting import get_search_tracking_metrics
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    SearchListingMatchRecord,
    TrackedSearchRecord,
    TrackingEventRecord,
)
from wallapop_tracker.storage.repositories import TrackedSearchRepository


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def item(price: str = "500") -> Listing:
    return Listing(
        item_id="listing-x",
        user_id="seller-x",
        title="MacBook Air M2",
        description="256GB, como nuevo",
        price=Decimal(price),
        currency="EUR",
        url="https://es.wallapop.com/item/listing-x",
    )


class FakeSearchClient:
    def __init__(self, value: Listing):
        self.value = value

    async def search_items(self, **kwargs):
        assert kwargs["query"]
        return [self.value]


def create_search(database: Database, query: str) -> int:
    with database.transaction() as session:
        return TrackedSearchRepository(session).create(query, name=query).id


@pytest.mark.asyncio
async def test_overlapping_searches_share_listing_and_new_alert(database):
    first = create_search(database, "macbook air m2")
    second = create_search(database, "macbook m2")

    result_a = await SearchTracker(FakeSearchClient(item()), database).track_search(first)
    result_b = await SearchTracker(FakeSearchClient(item()), database).track_search(second)

    assert result_a.new_listings == 1
    assert [alert.type for alert in result_a.alerts] == [AlertType.NEW_LISTING]
    assert result_b.new_listings == 0
    assert result_b.duplicates_suppressed == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ListingRecord)) == 1
        assert session.scalar(select(func.count()).select_from(SearchListingMatchRecord)) == 2
        assert session.scalar(select(func.count()).select_from(TrackingEventRecord)) == 1


@pytest.mark.asyncio
async def test_price_change_is_global_and_survives_service_restart(database):
    first = create_search(database, "macbook air m2")
    second = create_search(database, "macbook air")
    await SearchTracker(FakeSearchClient(item("500")), database).track_search(first)
    await SearchTracker(FakeSearchClient(item("500")), database).track_search(second)

    dropped_a = await SearchTracker(FakeSearchClient(item("450")), database).track_search(first)
    dropped_b = await SearchTracker(FakeSearchClient(item("450")), database).track_search(second)
    restarted = await SearchTracker(FakeSearchClient(item("450")), database).track_search(first)

    assert [alert.type for alert in dropped_a.alerts] == [AlertType.PRICE_DROP]
    assert dropped_a.alerts[0].old_price == Decimal("500")
    assert dropped_a.alerts[0].new_price == Decimal("450")
    assert dropped_b.price_changes == 0
    assert restarted.price_changes == 0
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(TrackingEventRecord)) == 2
        metrics = get_search_tracking_metrics(session, first)
        assert metrics.matched_listings == 1
        assert metrics.alerts_generated == 2


@pytest.mark.asyncio
async def test_filters_are_applied_before_persistence(database):
    search_id = create_search(database, "macbook")
    with database.transaction() as session:
        record = session.get(TrackedSearchRecord, search_id)
        assert record is not None
        record.filters_json = '{"include": ["256gb"], "exclude": ["roto"]}'

    filtered = await SearchTracker(FakeSearchClient(item()), database).track_search(search_id)
    assert filtered.matched_listings == 1

    with database.transaction() as session:
        record = session.get(TrackedSearchRecord, search_id)
        assert record is not None
        record.filters_json = '{"include": ["512gb"]}'
    rejected = await SearchTracker(FakeSearchClient(item()), database).track_search(search_id)
    assert rejected.matched_listings == 0
