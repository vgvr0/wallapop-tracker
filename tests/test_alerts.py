from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.models import Listing
from wallapop_tracker.services.alerts import PriceAlertService, SearchAlertService
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PriceWatchRecord,
    ProfileRecord,
    SavedSearchItemRecord,
    SavedSearchRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


@pytest.fixture
def database():
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.close()


def item(item_id: str, price: str = "100") -> Listing:
    return Listing(item_id=item_id, user_id="seller", title=item_id, price=Decimal(price))


def search() -> SavedSearchRecord:
    now = datetime.now(UTC)
    return SavedSearchRecord(name="phones", query="phone", created_at=now, updated_at=now)


@pytest.mark.asyncio
async def test_first_search_check_is_baseline(database):
    with database.transaction() as session:
        saved = search()
        session.add(saved)
        session.flush()
        service = SearchAlertService(session, _items([item("a"), item("b")]))
        assert await service.check_search(saved.id) == []
        assert len(session.scalars(select(SavedSearchItemRecord)).all()) == 2


@pytest.mark.asyncio
async def test_new_search_item_detected_and_existing_not_alerted(database):
    current = [item("a"), item("b")]

    async def runner(_: SavedSearchRecord):
        return current

    with database.transaction() as session:
        saved = search()
        session.add(saved)
        session.flush()
        service = SearchAlertService(session, runner)
        await service.check_search(saved.id)
        current.append(item("c"))
        alerts = await service.check_search(saved.id)
        assert [alert.listing_id for alert in alerts] == ["c"]
        assert (await service.check_search(saved.id)) == []


@pytest.mark.asyncio
async def test_new_item_on_second_page_detected(database):
    pages = [[item("a"), item("b")], [item("c"), item("d")]]

    async def runner(_: SavedSearchRecord):
        return [entry for page in pages for entry in page]

    with database.transaction() as session:
        saved = search()
        session.add(saved)
        session.flush()
        service = SearchAlertService(session, runner)
        assert await service.check_search(saved.id) == []
        pages[1].append(item("e"))
        alerts = await service.check_search(saved.id)
        assert len(alerts) == 1
        assert alerts[0].listing_id == "e"


@pytest.mark.asyncio
async def test_disabled_search_ignored(database):
    called = False

    async def runner(_: SavedSearchRecord):
        nonlocal called
        called = True
        return [item("a")]

    with database.transaction() as session:
        saved = search()
        saved.enabled = False
        session.add(saved)
        session.flush()
        assert await SearchAlertService(session, runner).check_search(saved.id) == []
        assert called is False


def test_search_item_uniqueness_and_independence(database):
    now = datetime.now(UTC)
    with database.transaction() as session:
        first, second = search(), search()
        first.name, second.name = "one", "two"
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                SavedSearchItemRecord(
                    saved_search_id=first.id,
                    wallapop_item_id="same",
                    first_seen_at=now,
                    last_seen_at=now,
                ),
                SavedSearchItemRecord(
                    saved_search_id=second.id,
                    wallapop_item_id="same",
                    first_seen_at=now,
                    last_seen_at=now,
                ),
            ]
        )


def _items(values: list[Listing]):
    async def runner(_: SavedSearchRecord):
        return values

    return runner


def _watch_session(database, prices: list[str], enabled: bool = True):
    now = datetime.now(UTC)
    with database.transaction() as session:
        suffix = str(
            session.scalar(select(ProfileRecord.id).order_by(ProfileRecord.id.desc())) or 0
        )
        profile = ProfileRecord(
            wallapop_user_id=f"seller-{suffix}",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
        listing = ListingRecord(
            wallapop_item_id=f"item-{suffix}",
            profile_id=profile.id,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(listing)
        session.flush()
        watch = PriceWatchRecord(listing_id=listing.id, created_at=now, enabled=enabled)
        session.add(watch)
        session.flush()
        for index, price in enumerate(prices):
            run = TrackingRunRecord(
                profile_id=profile.id,
                started_at=now + timedelta(minutes=index),
                finished_at=now + timedelta(minutes=index),
                status=TrackingRunStatus.VALID,
            )
            session.add(run)
            session.flush()
            session.add(
                ListingSnapshotRecord(
                    listing_id=listing.id,
                    tracking_run_id=run.id,
                    observed_at=run.started_at,
                    price=Decimal(price),
                )
            )
        session.commit()
        return PriceAlertService(session).check_price_watch(watch.id)


def test_price_alert_rules(database):
    assert _watch_session(database, ["100"]) is None
    alert = _watch_session(database, ["120", "100"])
    assert (
        alert
        and alert.type == AlertType.PRICE_DROP
        and alert.old_price == 120
        and alert.new_price == 100
    )
    assert _watch_session(database, ["100", "120"]) is None
    assert _watch_session(database, ["100", "100"]) is None


def test_disabled_price_watch_ignored(database):
    assert _watch_session(database, ["120", "100"], enabled=False) is None


def test_price_drop_not_repeated(database):
    now = datetime.now(UTC)
    with database.transaction() as session:
        profile = ProfileRecord(
            wallapop_user_id="seller-second",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
        listing = ListingRecord(
            wallapop_item_id="item-second",
            profile_id=profile.id,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(listing)
        session.flush()
        watch = PriceWatchRecord(listing_id=listing.id, created_at=now)
        session.add(watch)
        session.flush()
        for index, price in enumerate(("120", "100", "80")):
            run = TrackingRunRecord(
                profile_id=profile.id,
                started_at=now + timedelta(minutes=index),
                finished_at=now + timedelta(minutes=index),
                status=TrackingRunStatus.VALID,
            )
            session.add(run)
            session.flush()
            session.add(
                ListingSnapshotRecord(
                    listing_id=listing.id,
                    tracking_run_id=run.id,
                    observed_at=run.started_at,
                    price=Decimal(price),
                )
            )
        session.commit()
        service = PriceAlertService(session)
        first = service.check_price_watch(watch.id)
        assert first and first.new_price == 80
        assert service.check_price_watch(watch.id) is None
