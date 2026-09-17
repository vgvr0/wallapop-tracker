from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from wallapop_tracker.exceptions import WallapopError
from wallapop_tracker.models import Listing, Profile, ProfileStats, ReviewSummary
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    ProfileRecord,
    ProfileSnapshotRecord,
    TrackingRunListingRecord,
)
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


def profile() -> Profile:
    return Profile(
        user_id="user-1",
        name="Test User",
        slug="test-user-1",
        url="https://www.wallapop.com/user/test-user-1",
    )


def listing(price: str = "120") -> Listing:
    return Listing(
        item_id="item-1",
        user_id="user-1",
        title="Producto",
        description="Descripción",
        price=Decimal(price),
        currency="EUR",
        category_id="100",
        reserved=False,
        url="https://www.wallapop.com/item/producto-1",
    )


def test_schema_and_tracking_run_lifecycle(database):
    observed = datetime(2026, 9, 17, tzinfo=UTC)
    with database.session() as session:
        profile_record = ProfileRepository(session).get_or_create_profile(
            profile(), observed_at=observed
        )
        runs = TrackingRunRepository(session)
        run = runs.start_tracking_run(profile_record.id, started_at=observed)
        runs.finish_tracking_run(
            run.id,
            status="valid",
            finished_at=observed + timedelta(minutes=1),
            items_fetched=1,
            pages_fetched=1,
            profile_ok=True,
            stats_ok=True,
            reviews_ok=True,
            items_ok=True,
        )
        listing_record = ListingRepository(session).get_or_create_listing(
            listing(), profile_record.id, observed_at=observed
        )
        presence = SnapshotRepository(session).mark_listing_seen(
            run.id, listing_record.id, observed_at=observed
        )
        duplicate_presence = SnapshotRepository(session).mark_listing_seen(
            run.id, listing_record.id, observed_at=observed
        )
        assert presence.tracking_run_id == run.id
        assert duplicate_presence is presence
        assert SnapshotRepository(session).was_listing_seen_in_run(run.id, listing_record.id)
        assert session.scalar(select(func.count()).select_from(TrackingRunListingRecord)) == 1


def test_profile_and_listing_external_ids_are_reused_and_unique(database):
    with database.session() as session:
        profiles = ProfileRepository(session)
        first_profile = profiles.get_or_create_profile(profile())
        second_profile = profiles.get_or_create_profile(profile())
        assert second_profile.id == first_profile.id
        assert session.scalar(select(func.count()).select_from(ProfileRecord)) == 1

        listings = ListingRepository(session)
        first_listing = listings.get_or_create_listing(listing(), first_profile.id)
        second_listing = listings.get_or_create_listing(listing(), first_profile.id)
        assert second_listing.id == first_listing.id
        assert session.scalar(select(func.count()).select_from(ListingRecord)) == 1


def test_partial_run_cannot_create_presence(database):
    with database.session() as session:
        profile_record = ProfileRepository(session).get_or_create_profile(profile())
        run = TrackingRunRepository(session).start_tracking_run(profile_record.id)
        TrackingRunRepository(session).finish_tracking_run(run.id, status="partial")
        listing_record = ListingRepository(session).get_or_create_listing(
            listing(), profile_record.id
        )
        with pytest.raises(WallapopError, match="valid, complete"):
            SnapshotRepository(session).mark_listing_seen(run.id, listing_record.id)


def test_failed_run_cannot_create_presence(database):
    with database.session() as session:
        profile_record = ProfileRepository(session).get_or_create_profile(profile())
        run = TrackingRunRepository(session).start_tracking_run(profile_record.id)
        TrackingRunRepository(session).mark_failed(run.id)
        listing_record = ListingRepository(session).get_or_create_listing(
            listing(), profile_record.id
        )
        with pytest.raises(WallapopError, match="valid, complete"):
            SnapshotRepository(session).mark_listing_seen(run.id, listing_record.id)
        assert session.scalar(select(func.count()).select_from(TrackingRunListingRecord)) == 0


def test_change_based_snapshots_and_presence_are_independent(database):
    observed_1 = datetime(2026, 9, 17, tzinfo=UTC)
    observed_2 = datetime(2026, 9, 24, tzinfo=UTC)
    with database.session() as session:
        profile_record = ProfileRepository(session).get_or_create_profile(
            profile(), observed_at=observed_1
        )
        run_repo = TrackingRunRepository(session)
        snap_repo = SnapshotRepository(session)
        listing_record = ListingRepository(session).get_or_create_listing(
            listing(), profile_record.id, observed_at=observed_1
        )
        run_1 = run_repo.start_tracking_run(profile_record.id, started_at=observed_1)
        run_repo.finish_tracking_run(run_1.id, status="valid", items_ok=True)
        assert snap_repo.save_listing_snapshot(
            listing_record.id, run_1.id, listing(), observed_at=observed_1
        )
        snap_repo.mark_listing_seen(run_1.id, listing_record.id, observed_at=observed_1)

        run_2 = run_repo.start_tracking_run(profile_record.id, started_at=observed_2)
        run_repo.finish_tracking_run(run_2.id, status="valid", items_ok=True)
        assert (
            snap_repo.save_listing_snapshot(
                listing_record.id, run_2.id, listing(), observed_at=observed_2
            )
            is None
        )
        snap_repo.mark_listing_seen(run_2.id, listing_record.id, observed_at=observed_2)

        run_3 = run_repo.start_tracking_run(profile_record.id, started_at=observed_2)
        run_repo.finish_tracking_run(run_3.id, status="valid", items_ok=True)
        assert snap_repo.save_listing_snapshot(
            listing_record.id, run_3.id, listing("100"), observed_at=observed_2
        )

        assert len(session.scalars(select(ListingSnapshotRecord)).all()) == 2
        session.commit()

    with database.session() as session:
        assert (
            session.scalar(
                select(ListingSnapshotRecord).where(ListingSnapshotRecord.listing_id == 1)
            )
            is not None
        )
        assert len(session.scalars(select(TrackingRunListingRecord)).all()) == 2


def test_profile_snapshot_changes_only_on_metric_change(database):
    with database.session() as session:
        profile_record = ProfileRepository(session).get_or_create_profile(profile())
        run_repo = TrackingRunRepository(session)
        snap_repo = SnapshotRepository(session)
        stats = ProfileStats(rating=4.9, review_count=2, published_count=1, sold_count=0)
        reviews = ReviewSummary(rating=4.9, review_count=2, rating_distribution={5: 100})
        run_1 = run_repo.start_tracking_run(profile_record.id)
        run_repo.finish_tracking_run(run_1.id, status="valid")
        assert snap_repo.save_profile_snapshot(profile_record.id, run_1.id, stats, reviews)
        run_2 = run_repo.start_tracking_run(profile_record.id)
        run_repo.finish_tracking_run(run_2.id, status="valid")
        assert snap_repo.save_profile_snapshot(profile_record.id, run_2.id, stats, reviews) is None
        assert len(session.scalars(select(ProfileSnapshotRecord)).all()) == 1


def test_removed_and_reappeared_are_explicit_snapshot_states(database):
    t1 = datetime(2026, 9, 17, tzinfo=UTC)
    t2 = datetime(2026, 9, 24, tzinfo=UTC)
    t3 = datetime(2026, 10, 8, tzinfo=UTC)
    with database.session() as session:
        p = ProfileRepository(session).get_or_create_profile(profile(), observed_at=t1)
        listings = ListingRepository(session)
        snapshots = SnapshotRepository(session)
        runs = TrackingRunRepository(session)
        item = listings.get_or_create_listing(listing(), p.id, observed_at=t1)

        r1 = runs.start_tracking_run(p.id, started_at=t1)
        runs.mark_valid(r1.id, items_ok=True)
        snapshots.save_listing_snapshot(item.id, r1.id, listing(), observed_at=t1)

        r2 = runs.start_tracking_run(p.id, started_at=t2)
        runs.mark_valid(r2.id, items_ok=True)
        removed = snapshots.save_listing_snapshot(
            item.id, r2.id, None, observed_at=t2, presence_state=PresenceState.REMOVED
        )
        assert removed is not None
        assert removed.presence_state == PresenceState.REMOVED

        r3 = runs.start_tracking_run(p.id, started_at=t3)
        runs.mark_valid(r3.id, items_ok=True)
        active = snapshots.save_listing_snapshot(item.id, r3.id, listing(), observed_at=t3)
        assert active is not None
        assert active.presence_state == PresenceState.ACTIVE
        assert listings.get_listing_by_wallapop_id("item-1").id == item.id
        states = [
            row.presence_state
            for row in session.scalars(
                select(ListingSnapshotRecord).order_by(ListingSnapshotRecord.observed_at)
            )
        ]
        assert states == [PresenceState.ACTIVE, PresenceState.REMOVED, PresenceState.ACTIVE]


def test_invalid_run_does_not_change_last_seen_at(database):
    t1 = datetime(2026, 9, 17, tzinfo=UTC)
    t2 = datetime(2026, 9, 24, tzinfo=UTC)
    with database.session() as session:
        profile_record = ProfileRepository(session).get_or_create_profile(profile(), observed_at=t1)
        listing_record = ListingRepository(session).get_or_create_listing(
            listing(), profile_record.id, observed_at=t1
        )
        run = TrackingRunRepository(session).start_tracking_run(profile_record.id, started_at=t2)
        TrackingRunRepository(session).mark_partial(run.id)
        assert listing_record.last_seen_at == t1
        assert profile_record.last_seen_at == t1


def test_repository_changes_rollback_without_commit(database):
    session = database.session_factory()
    try:
        ProfileRepository(session).get_or_create_profile(profile())
        session.rollback()
    finally:
        session.close()

    with database.session() as verification:
        assert verification.scalar(select(func.count()).select_from(ProfileRecord)) == 0
