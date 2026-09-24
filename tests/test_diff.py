from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from wallapop_tracker.domain.changes import ChangeType
from wallapop_tracker.models import Listing, Profile, ProfileStats
from wallapop_tracker.services.diff import DiffService
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


def _listing(
    item_id: str = "item-1", price: str = "120", title: str = "Producto", reserved: bool = False
) -> Listing:
    return Listing(
        item_id=item_id,
        user_id="user-1",
        title=title,
        description="Descripción",
        price=Decimal(price),
        currency="EUR",
        category_id="100",
        reserved=reserved,
    )


def _setup(session):
    profile = ProfileRepository(session).get_or_create_profile(
        Profile(user_id="user-1", name="Test", slug="test"),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    listing = ListingRepository(session).get_or_create_listing(
        _listing(), profile.id, observed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    return profile, listing


def _run(session, profile_id: int, number: int, listing_ids: tuple[int, ...] = ()):
    at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=number)
    runs = TrackingRunRepository(session)
    run = runs.start_tracking_run(profile_id, started_at=at)
    runs.mark_valid(run.id, items_ok=True)
    snapshots = SnapshotRepository(session)
    for listing_id in listing_ids:
        snapshots.mark_listing_seen(run.id, listing_id, observed_at=at)
    return run


def test_first_run_is_baseline(database):
    with database.session() as session:
        profile, listing = _setup(session)
        first = _run(session, profile.id, 1, (listing.id,))
        assert DiffService(session).compare_runs(None, first.id) == []


def test_presence_lifecycle_and_invalid_runs_are_ignored(database):
    with database.session() as session:
        profile, listing = _setup(session)
        first = _run(session, profile.id, 1, (listing.id,))
        removed = _run(session, profile.id, 2)
        partial = TrackingRunRepository(session).start_tracking_run(
            profile.id, started_at=datetime(2026, 1, 4, tzinfo=UTC)
        )
        TrackingRunRepository(session).mark_partial(partial.id)
        reappeared = _run(session, profile.id, 4, (listing.id,))
        service = DiffService(session)
        assert [c.change_type for c in service.compare_runs(first.id, removed.id)] == [
            ChangeType.REMOVED
        ]
        assert [c.change_type for c in service.compare_runs(removed.id, reappeared.id)] == [
            ChangeType.REAPPEARED
        ]


def test_new_listing_and_field_changes(database):
    with database.session() as session:
        profile, first_listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (first_listing.id,))
        snapshots.save_listing_snapshot(
            first_listing.id, first.id, _listing(), observed_at=first.started_at
        )
        second_listing = ListingRepository(session).get_or_create_listing(
            _listing("item-2"), profile.id, observed_at=first.started_at
        )
        second = _run(session, profile.id, 2, (first_listing.id, second_listing.id))
        snapshots.save_listing_snapshot(
            first_listing.id,
            second.id,
            _listing(price="100", title="Nuevo", reserved=True),
            observed_at=second.started_at,
        )
        changes = DiffService(session).compare_runs(first.id, second.id)
        assert [c.change_type for c in changes] == [
            ChangeType.NEW_LISTING,
            ChangeType.PRICE_CHANGED,
            ChangeType.TITLE_CHANGED,
            ChangeType.RESERVED,
        ]


@pytest.mark.parametrize(
    ("field", "old", "new", "kind"),
    [
        ("review_count", 2, 3, ChangeType.REVIEW_COUNT_CHANGED),
        ("rating", 4.0, 4.5, ChangeType.RATING_CHANGED),
        ("sold_count", 10, 11, ChangeType.SOLD_COUNT_CHANGED),
        ("reports_received", 8, 11, ChangeType.REPORTS_RECEIVED_CHANGED),
    ],
)
def test_profile_metric_changes(database, field, old, new, kind):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        second = _run(session, profile.id, 2, (listing.id,))
        snapshots.save_profile_snapshot(profile.id, first.id, ProfileStats(**{field: old}))
        snapshots.save_profile_snapshot(profile.id, second.id, ProfileStats(**{field: new}))
        changes = DiffService(session).compare_runs(first.id, second.id)
        assert [(c.change_type, c.old_value, c.new_value) for c in changes] == [(kind, old, new)]


@pytest.mark.parametrize("old,new", [(None, 5), (5, None), (None, None)])
def test_optional_reports_received_transitions_are_not_changes(database, old, new):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        second = _run(session, profile.id, 2, (listing.id,))
        snapshots.save_profile_snapshot(profile.id, first.id, ProfileStats(reports_received=old))
        snapshots.save_profile_snapshot(profile.id, second.id, ProfileStats(reports_received=new))
        assert DiffService(session).compare_runs(first.id, second.id) == []


def test_change_based_listing_snapshot_is_reconstructed(database):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        snapshots.save_listing_snapshot(
            listing.id, first.id, _listing(), observed_at=first.started_at
        )
        second = _run(session, profile.id, 2, (listing.id,))
        third = _run(session, profile.id, 3, (listing.id,))
        snapshots.save_listing_snapshot(
            listing.id, third.id, _listing(price="100"), observed_at=third.started_at
        )
        assert DiffService(session).compare_runs(first.id, second.id) == []
        changes = DiffService(session).compare_runs(second.id, third.id)
        assert [(c.change_type, c.old_value, c.new_value) for c in changes] == [
            (ChangeType.PRICE_CHANGED, Decimal("120.00"), Decimal("100.00"))
        ]


def test_diff_is_idempotent(database):
    with database.session() as session:
        profile, listing = _setup(session)
        first = _run(session, profile.id, 1, (listing.id,))
        second = _run(session, profile.id, 2)
        service = DiffService(session)
        assert service.compare_runs(first.id, second.id) == service.compare_runs(
            first.id, second.id
        )


def _listing_change(database, field, old_value, new_value):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        base = _listing().model_copy(update={field: old_value})
        snapshots.save_listing_snapshot(listing.id, first.id, base, observed_at=first.started_at)
        second = _run(session, profile.id, 2, (listing.id,))
        changed = base.model_copy(update={field: new_value})
        snapshots.save_listing_snapshot(
            listing.id, second.id, changed, observed_at=second.started_at
        )
        changes = DiffService(session).compare_runs(first.id, second.id)
        assert len(changes) == 1
        assert changes[0].old_value == old_value
        assert changes[0].new_value == new_value
        return changes[0].change_type


def test_shipping_available_changed(database):
    assert (
        _listing_change(database, "shipping_available", True, False)
        == ChangeType.SHIPPING_AVAILABLE_CHANGED
    )


def test_shipping_available_same_no_change(database):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        value = _listing()
        snapshots.save_listing_snapshot(listing.id, first.id, value, observed_at=first.started_at)
        second = _run(session, profile.id, 2, (listing.id,))
        assert (
            snapshots.save_listing_snapshot(
                listing.id, second.id, value, observed_at=second.started_at
            )
            is None
        )
        assert DiffService(session).compare_runs(first.id, second.id) == []


def test_brand_changed(database):
    assert _listing_change(database, "brand", "Apple", "Samsung") == ChangeType.BRAND_CHANGED


def test_brand_none_to_value(database):
    assert _listing_change(database, "brand", None, "Apple") == ChangeType.BRAND_CHANGED


def test_brand_same_no_change(database):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        value = _listing().model_copy(update={"brand": "Apple"})
        snapshots.save_listing_snapshot(listing.id, first.id, value, observed_at=first.started_at)
        second = _run(session, profile.id, 2, (listing.id,))
        assert (
            snapshots.save_listing_snapshot(
                listing.id, second.id, value, observed_at=second.started_at
            )
            is None
        )
        assert DiffService(session).compare_runs(first.id, second.id) == []


def test_change_based_shipping_reconstruction(database):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        snapshots.save_listing_snapshot(
            listing.id,
            first.id,
            _listing().model_copy(update={"shipping_available": True}),
            observed_at=first.started_at,
        )
        middle = _run(session, profile.id, 2, (listing.id,))
        third = _run(session, profile.id, 3, (listing.id,))
        snapshots.save_listing_snapshot(
            listing.id,
            third.id,
            _listing().model_copy(update={"shipping_available": False}),
            observed_at=third.started_at,
        )
        assert DiffService(session).compare_runs(first.id, middle.id) == []
        changes = DiffService(session).compare_runs(middle.id, third.id)
        assert [(change.change_type, change.old_value, change.new_value) for change in changes] == [
            (ChangeType.SHIPPING_AVAILABLE_CHANGED, True, False)
        ]


def test_change_based_brand_reconstruction(database):
    with database.session() as session:
        profile, listing = _setup(session)
        snapshots = SnapshotRepository(session)
        first = _run(session, profile.id, 1, (listing.id,))
        snapshots.save_listing_snapshot(
            listing.id,
            first.id,
            _listing().model_copy(update={"brand": "Apple"}),
            observed_at=first.started_at,
        )
        middle = _run(session, profile.id, 2, (listing.id,))
        third = _run(session, profile.id, 3, (listing.id,))
        snapshots.save_listing_snapshot(
            listing.id,
            third.id,
            _listing().model_copy(update={"brand": "Samsung"}),
            observed_at=third.started_at,
        )
        assert DiffService(session).compare_runs(first.id, middle.id) == []
        changes = DiffService(session).compare_runs(middle.id, third.id)
        assert [(change.change_type, change.old_value, change.new_value) for change in changes] == [
            (ChangeType.BRAND_CHANGED, "Apple", "Samsung")
        ]
