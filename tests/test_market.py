from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.reporting.market import (
    get_activity_time_series,
    get_brand_market_stats,
    get_listing_market_estimate,
    get_market_summary,
    get_price_distribution,
    get_price_time_series,
    get_seller_market_stats,
)
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PossibleRelistingRecord,
    PresenceState,
    TrackingEventRecord,
    TrackingRunListingRecord,
)
from wallapop_tracker.storage.repositories import (
    SearchMatchRepository,
    TrackedSearchRepository,
    TrackingRunRepository,
)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def make_dataset(database: Database) -> tuple[int, datetime, datetime, datetime]:
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = first + timedelta(days=1)
    third = first + timedelta(days=2)
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create("phones", notify_on_first_run=True)
        runs = TrackingRunRepository(session)
        run1 = runs.start_search_run(search.id, started_at=first)
        runs.mark_valid(run1.id, items_fetched=2, items_ok=True)
        run2 = runs.start_search_run(search.id, started_at=second)
        runs.mark_valid(run2.id, items_fetched=3, items_ok=True)
        run3 = runs.start_search_run(search.id, started_at=third)
        runs.mark_valid(run3.id, items_fetched=2, items_ok=True)
        listings = [
            ListingRecord(
                wallapop_item_id="a",
                seller_user_id="seller-a",
                first_seen_at=first,
                last_seen_at=second,
                created_at=first,
                updated_at=first,
            ),
            ListingRecord(
                wallapop_item_id="b",
                seller_user_id="seller-a",
                first_seen_at=first,
                last_seen_at=third,
                created_at=first,
                updated_at=first,
            ),
            ListingRecord(
                wallapop_item_id="c",
                seller_user_id="seller-b",
                first_seen_at=second,
                last_seen_at=third,
                created_at=second,
                updated_at=second,
            ),
        ]
        session.add_all(listings)
        session.flush()
        matches = SearchMatchRepository(session)
        for run, present in ((run1, listings[:2]), (run2, listings), (run3, listings[1:])):
            for listing in present:
                matches.touch(search.id, listing.id, run.started_at)
                session.add(
                    TrackingRunListingRecord(
                        tracking_run_id=run.id,
                        listing_id=listing.id,
                        observed_at=run.started_at,
                    )
                )
        snapshots = [
            (listings[0], run1, first, Decimal("100"), "Apple"),
            (listings[1], run1, first, Decimal("200"), "Samsung"),
            (listings[0], run2, second, Decimal("150"), "Apple"),
            (listings[1], run2, second, Decimal("200"), "Samsung"),
            (listings[2], run2, second, Decimal("300"), "Apple"),
            (listings[1], run3, third, Decimal("190"), "Samsung"),
            (listings[2], run3, third, Decimal("300"), "Apple"),
        ]
        for listing, run, observed, price, brand in snapshots:
            session.add(
                ListingSnapshotRecord(
                    listing_id=listing.id,
                    tracking_run_id=run.id,
                    observed_at=observed,
                    title=listing.wallapop_item_id,
                    price=price,
                    brand=brand,
                    category_id="phones",
                    category_name="Phones",
                    presence_state=PresenceState.ACTIVE,
                )
            )
        session.add_all(
            [
                TrackingEventRecord(
                    event_type=AlertType.PRICE_DROP,
                    idempotency_key="drop-b",
                    listing_id=listings[1].id,
                    tracking_run_id=run3.id,
                    tracked_search_id=search.id,
                    old_price=Decimal("200"),
                    new_price=Decimal("190"),
                    created_at=third,
                ),
                TrackingEventRecord(
                    event_type=AlertType.PRICE_INCREASE,
                    idempotency_key="increase-a",
                    listing_id=listings[0].id,
                    tracking_run_id=run2.id,
                    tracked_search_id=search.id,
                    old_price=Decimal("100"),
                    new_price=Decimal("150"),
                    created_at=second,
                ),
            ]
        )
        return search.id, first, second, third


def test_market_prices_activity_and_duration(database):
    search_id, first, _, third = make_dataset(database)
    with database.session() as session:
        summary = get_market_summary(session, search_id, start_at=first, end_at=third)
        assert summary.active_listings == 2
        assert summary.unique_listings == 3
        assert summary.median_price == Decimal("245")
        assert summary.average_price == Decimal("245")
        assert summary.p25_price == Decimal("217.5")
        assert summary.p75_price == Decimal("272.5")
        assert summary.new_listings == 3
        assert summary.removed_listings == 1
        assert summary.price_drops == 1
        assert summary.price_increases == 1
        assert summary.median_active_duration == timedelta(days=2)

        distribution = get_price_distribution(session, search_id, bins=2, end_at=third)
        assert [bin.count for bin in distribution] == [1, 1]
        assert get_price_time_series(session, search_id)
        activity = get_activity_time_series(session, search_id)
        assert sum(point.removed_listings for point in activity) == 1


def test_market_seller_and_brand_aggregation(database):
    search_id, _, _, third = make_dataset(database)
    with database.session() as session:
        sellers = get_seller_market_stats(session, search_id, end_at=third)
        assert sellers[0].seller_external_id == "seller-a"
        assert sellers[0].listing_count == 2
        brands = get_brand_market_stats(session, search_id, end_at=third)
        assert [(brand.brand, brand.listing_count) for brand in brands] == [
            ("Apple", 2),
            ("Samsung", 1),
        ]


def test_market_missing_prices_empty_and_period_filter(database):
    search_id, first, second, third = make_dataset(database)
    with database.transaction() as session:
        snapshot = session.scalar(select(ListingSnapshotRecord))
        assert snapshot is not None
        snapshot.price = None
    with database.session() as session:
        summary = get_market_summary(session, search_id, start_at=third, end_at=third)
        assert summary.new_listings == 0
        assert summary.median_price == Decimal("245")
        empty = get_market_summary(session, 999, start_at=first, end_at=second)
        assert empty.unique_listings == 0
        assert empty.median_price is None


def test_market_scope_is_per_search_and_relistings_do_not_merge(database):
    search_id, first, _, third = make_dataset(database)
    with database.transaction() as session:
        search_b = TrackedSearchRepository(session).create("same listing")
        run = TrackingRunRepository(session).start_search_run(search_b.id, started_at=first)
        TrackingRunRepository(session).mark_valid(run.id, items_fetched=1, items_ok=True)
        listing = session.scalar(select(ListingRecord).where(ListingRecord.wallapop_item_id == "a"))
        assert listing is not None
        SearchMatchRepository(session).touch(search_b.id, listing.id, first)
        session.add(
            TrackingRunListingRecord(
                tracking_run_id=run.id, listing_id=listing.id, observed_at=first
            )
        )
        other = session.scalar(select(ListingRecord).where(ListingRecord.wallapop_item_id == "b"))
        assert other is not None
        session.add(
            PossibleRelistingRecord(
                previous_listing_id=listing.id,
                current_listing_id=other.id,
                score=Decimal("0.8000"),
                reasons_json="[]",
                detected_at=third,
            )
        )
    with database.session() as session:
        first_summary = get_market_summary(session, search_id, end_at=third)
        second_summary = get_market_summary(session, search_b.id, end_at=first)
        assert first_summary.unique_listings == 3
        assert first_summary.possible_relisting_count == 1
        assert second_summary.unique_listings == 1
        assert second_summary.active_listings == 1


def test_market_cli_summary(database, tmp_path, monkeypatch):
    path = tmp_path / "market.db"
    file_db = Database(f"sqlite:///{path}")
    file_db.create_all()
    search_id, _, _, _ = make_dataset(file_db)
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    result = CliRunner().invoke(cli.app, ["analytics", "market", str(search_id)])
    assert result.exit_code == 0, result.output
    assert "Active listings:" in result.output
    file_db.close()


def test_listing_market_sold_requires_explicit_status(database):
    _, _, _, _ = make_dataset(database)
    with database.transaction() as session:
        rows = session.scalars(
            select(ListingSnapshotRecord).order_by(ListingSnapshotRecord.id)
        ).all()
        sold = rows[-1]
        target = rows[-2]
        sold.sale_status = "sold"
        sold.title = target.title
        sold.brand = target.brand
        target_id = target.listing_id
    with database.session() as session:
        estimate = get_listing_market_estimate(session, target_id)
        assert estimate.sold_last_asking_price_count == 1
        assert estimate.sold_last_asking_price_median == Decimal("300")
