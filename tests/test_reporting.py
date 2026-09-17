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
        assert {
            item.wallapop_item_id for item in get_current_inventory(session, profile.id)
        } == {"a", "c"}
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
            1, Decimal("73.33333333333333333333333333")
        )
        assert summary[1].priced_listing_count == 3
        assert get_average_active_price(session, profile.id, all_runs[1].id) == (
            Decimal("73.33333333333333333333333333"), 3
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
            True, False, False, True
        ]
        assert [point.presence_state.value for point in get_price_history(session, listing.id)] == [
            "active", "removed", "active"
        ]
        duration = get_approx_active_duration(session, listing.id)
        assert duration is not None
        assert duration.approx_active_duration.days == 21
