from datetime import UTC, datetime
from decimal import Decimal

import pytest

from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary
from wallapop_tracker.reporting import (
    get_approx_active_duration,
    get_average_active_price,
    get_current_inventory,
    get_inventory_history,
    get_new_listings_between_runs,
    get_presence_history,
    get_price_history,
    get_profile_inventory_stats,
    get_profile_metrics_history,
    get_removed_listings_between_runs,
    get_weekly_summary,
)
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    ProfileRepository,
    SnapshotRepository,
    TrackingRunRepository,
)


@pytest.fixture
def database():
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.close()


def test_reporting_reconstructs_history_and_excludes_partial(database):
    times = [datetime(2026, 9, day, tzinfo=UTC) for day in (3, 10, 17, 24)]
    with database.session() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="reporting-user"), observed_at=times[0]
        )
        runs = TrackingRunRepository(session)
        snapshots = SnapshotRepository(session)
        listings = ListingRepository(session)
        all_runs = []
        listing_records = {}
        weeks = [
            ([("a", "100"), ("b", "50")], 10, 3, True),
            ([("a", "90"), ("b", "50"), ("c", "80")], 12, 4, True),
            ([("a", "90"), ("b", "50"), ("c", "80")], 12, 4, False),
            ([("a", "90"), ("c", "80")], 13, 5, True),
        ]
        for index, (items, reviews, sold, valid) in enumerate(weeks):
            run = runs.start_tracking_run(profile.id, started_at=times[index])
            runs.finish_tracking_run(run.id, status="valid" if valid else "partial", items_ok=valid)
            all_runs.append(run)
            if not valid:
                continue
            snapshots.save_profile_snapshot(
                profile.id,
                run.id,
                ProfileStats(review_count=reviews, sold_count=sold),
                ReviewSummary(review_count=reviews),
                observed_at=times[index],
            )
            for item_id, price in items:
                record = listing_records.get(item_id)
                if record is None:
                    record = listings.get_or_create_listing(
                        Listing(item_id=item_id, user_id="reporting-user", price=Decimal(price)),
                        profile.id,
                        observed_at=times[index],
                    )
                    listing_records[item_id] = record
                value = Listing(item_id=item_id, user_id="reporting-user", price=Decimal(price))
                snapshots.save_listing_snapshot(record.id, run.id, value, observed_at=times[index])
                snapshots.mark_listing_seen(run.id, record.id, observed_at=times[index])
        session.commit()

        assert [point.count for point in get_inventory_history(session, profile.id)] == [2, 3, 2]
        assert {item.wallapop_item_id for item in get_current_inventory(session, profile.id)} == {
            "a",
            "c",
        }
        assert get_new_listings_between_runs(session, all_runs[0].id, all_runs[1].id) == [
            listing_records["c"].id
        ]
        assert get_removed_listings_between_runs(session, all_runs[1].id, all_runs[3].id) == [
            listing_records["b"].id
        ]
        assert [
            point.review_count for point in get_profile_metrics_history(session, profile.id)
        ] == [10, 12, 13]
        summary = get_weekly_summary(session, profile.id)
        assert [point.sold_count_delta for point in summary] == [None, 1, 1]
        assert (summary[1].price_decreases, summary[1].average_active_price) == (
            1,
            Decimal("73.33333333333333333333333333"),
        )
        assert summary[1].priced_listing_count == 3
        assert get_average_active_price(session, profile.id, all_runs[1].id) == (
            Decimal("73.33333333333333333333333333"),
            3,
        )


def test_price_and_presence_history_include_reappearance(database):
    times = [datetime(2026, 9, day, tzinfo=UTC) for day in (3, 10, 17, 24)]
    with database.session() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="u"), observed_at=times[0]
        )
        listing = ListingRepository(session).get_or_create_listing(
            Listing(item_id="a", user_id="u", price=Decimal("100")),
            profile.id,
            observed_at=times[0],
        )
        runs = TrackingRunRepository(session)
        snapshots = SnapshotRepository(session)
        for index, present in enumerate((True, False, False, True)):
            run = runs.start_tracking_run(profile.id, started_at=times[index])
            runs.mark_valid(run.id, items_ok=True)
            if present:
                value = Listing(item_id="a", user_id="u", price=Decimal("100"))
                snapshots.save_listing_snapshot(listing.id, run.id, value, observed_at=times[index])
                snapshots.mark_listing_seen(run.id, listing.id, observed_at=times[index])
        session.commit()

        assert [point.present for point in get_presence_history(session, listing.id)] == [
            True,
            False,
            False,
            True,
        ]
        assert [point.presence_state.value for point in get_price_history(session, listing.id)] == [
            "active",
            "removed",
            "active",
        ]
        duration = get_approx_active_duration(session, listing.id)
        assert duration is not None
        assert duration.approx_active_duration.days == 21


def _save_inventory_run(session, profile_id, observed_at, values):
    runs = TrackingRunRepository(session)
    snapshots = SnapshotRepository(session)
    listings = ListingRepository(session)
    run = runs.start_tracking_run(profile_id, started_at=observed_at)
    runs.mark_valid(run.id, items_ok=True)
    for item_id, price, currency, sale_status, presence_state in values:
        record = listings.get_or_create_listing(
            Listing(item_id=item_id, user_id="inventory-user", price=price, currency=currency),
            profile_id,
            observed_at=observed_at,
        )
        value = Listing(
            item_id=item_id,
            user_id="inventory-user",
            price=price,
            currency=currency,
            sale_status=sale_status,
        )
        snapshots.save_listing_snapshot(
            record.id,
            run.id,
            None if presence_state == "removed" else value,
            observed_at=observed_at,
            presence_state=presence_state,
        )
        if presence_state == "active":
            snapshots.mark_listing_seen(run.id, record.id, observed_at=observed_at)
    return run


def test_profile_inventory_stats_use_latest_complete_state_and_decimal_math(database):
    first = datetime(2026, 9, 3, tzinfo=UTC)
    latest = datetime(2026, 9, 10, tzinfo=UTC)
    with database.session() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="inventory-user"), observed_at=first
        )
        _save_inventory_run(
            session,
            profile.id,
            first,
            [
                ("changed", Decimal("999.99"), "EUR", "active", "active"),
                ("removed", Decimal("20.00"), "EUR", "active", "active"),
            ],
        )
        _save_inventory_run(
            session,
            profile.id,
            latest,
            [
                ("changed", Decimal("120.00"), "EUR", "active", "active"),
                ("reserved", Decimal("50.00"), "EUR", "reserved", "active"),
                ("sold", Decimal("40.00"), "EUR", "sold", "active"),
                ("removed", Decimal("20.00"), "EUR", "active", "removed"),
                ("unknown-price", None, "EUR", "active", "active"),
                ("small-a", Decimal("1.10"), "EUR", "active", "active"),
                ("small-b", Decimal("2.20"), "EUR", "reserved", "active"),
                ("small-c", Decimal("3.30"), "EUR", "active", "active"),
            ],
        )
        # A later partial run must not become authoritative.
        partial = TrackingRunRepository(session).start_tracking_run(
            profile.id, started_at=datetime(2026, 9, 11, tzinfo=UTC)
        )
        TrackingRunRepository(session).finish_tracking_run(
            partial.id, status="partial", items_ok=False
        )

        stats = get_profile_inventory_stats(session, profile.id, "seller")

    assert stats.active_count == 4
    assert stats.reserved_count == 2
    assert stats.total_count == 6
    assert stats.priced_count == 5
    assert stats.currency == "EUR"
    assert stats.total_value == Decimal("176.60")
    assert stats.average_price == Decimal("35.32")
    assert stats.median_price == Decimal("3.30")
    assert stats.minimum_price == Decimal("1.10")
    assert stats.maximum_price == Decimal("120.00")


def test_profile_inventory_stats_median_even_and_empty_results(database):
    observed = datetime(2026, 9, 12, tzinfo=UTC)
    with database.session() as session:
        empty_profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="empty"), observed_at=observed
        )
        empty = get_profile_inventory_stats(session, empty_profile.id, "empty")
        assert empty.total_count == 0
        assert empty.priced_count == 0
        assert empty.total_value == Decimal("0")
        assert empty.average_price is None
        assert empty.median_price is None

        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="even"), observed_at=observed
        )
        _save_inventory_run(
            session,
            profile.id,
            observed,
            [
                ("one", Decimal("1.00"), "EUR", "active", "active"),
                ("two", Decimal("2.00"), "EUR", "active", "active"),
                ("three", Decimal("3.00"), "EUR", "active", "active"),
                ("four", Decimal("4.00"), "EUR", "active", "active"),
            ],
        )
        stats = get_profile_inventory_stats(session, profile.id, "even")

    assert stats.total_count == 4
    assert stats.median_price == Decimal("2.50")


def test_profile_inventory_stats_reject_mixed_currencies(database):
    observed = datetime(2026, 9, 13, tzinfo=UTC)
    with database.session() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="mixed"), observed_at=observed
        )
        _save_inventory_run(
            session,
            profile.id,
            observed,
            [
                ("eur", Decimal("10.00"), "EUR", "active", "active"),
                ("usd", Decimal("10.00"), "USD", "active", "active"),
            ],
        )

        with pytest.raises(ValueError, match="mixed or unknown currencies"):
            get_profile_inventory_stats(session, profile.id, "mixed")
