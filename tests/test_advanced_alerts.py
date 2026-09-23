from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from wallapop_tracker.domain.advanced_alerts import detect_price_alerts
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    TrackedSearchRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)

NOW = datetime(2026, 1, 31, 12, tzinfo=UTC)


def snapshot(snapshot_id: int, price: str | None, at: datetime) -> ListingSnapshotRecord:
    return ListingSnapshotRecord(
        id=snapshot_id,
        listing_id=1,
        tracking_run_id=snapshot_id,
        observed_at=at,
        price=Decimal(price) if price is not None else None,
    )


def config(**values: object) -> SimpleNamespace:
    defaults = dict(
        target_price=None,
        percentage_drop_threshold=None,
        notify_on_30d_low=False,
        notify_on_90d_low=False,
        notify_on_all_time_low=False,
    )
    defaults.update(values)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [("600", "550", False), ("550", "500", True), ("550", "480", True), ("480", "470", False)],
)
def test_target_price_crossings(database, old, new, expected):
    with database.session() as session:
        events = detect_price_alerts(
            session,
            listing_id=1,
            run_id=2,
            tracked_search_id=None,
            previous=snapshot(1, old, NOW - timedelta(days=1)),
            current=snapshot(2, new, NOW),
            config=config(target_price=Decimal("500")),
        )
    assert bool([e for e in events if e["event_type"] == "TARGET_PRICE_REACHED"]) is expected


@pytest.mark.parametrize(
    ("new", "expected"), [("91", False), ("90", True), ("80", True), ("110", False), ("100", False)]
)
def test_percentage_drop_and_metadata(database, new, expected):
    with database.session() as session:
        events = detect_price_alerts(
            session,
            listing_id=1,
            run_id=2,
            tracked_search_id=None,
            previous=snapshot(1, "100", NOW - timedelta(days=1)),
            current=snapshot(2, new, NOW),
            config=config(percentage_drop_threshold=Decimal("10")),
        )
    drops = [e for e in events if e["event_type"] == "PERCENTAGE_DROP"]
    assert bool(drops) is expected
    if drops:
        assert (
            drops[0]["metadata"]["drop_percentage"] == Decimal("10")
            if new == "90"
            else drops[0]["metadata"]["threshold"] == Decimal("10")
        )


def test_price_drop_invalid_values_are_silent(database):
    with database.session() as session:
        for old, new in (("0", "1"), (None, "90"), ("100", None)):
            assert (
                detect_price_alerts(
                    session,
                    listing_id=1,
                    run_id=2,
                    tracked_search_id=None,
                    previous=snapshot(1, old, NOW - timedelta(days=1)),
                    current=snapshot(2, new, NOW),
                    config=config(percentage_drop_threshold=Decimal("10")),
                )
                == []
            )


def test_historical_lows_use_previous_window_only(database):
    with database.transaction() as session:
        session.add(
            ListingRecord(
                id=1,
                wallapop_item_id="1",
                first_seen_at=NOW,
                last_seen_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(TrackedSearchRecord(id=1, query="x", created_at=NOW, updated_at=NOW))
        session.flush()
        session.add_all(
            [
                TrackingRunRecord(
                    id=i,
                    tracked_search_id=1,
                    started_at=NOW,
                    finished_at=NOW,
                    status=TrackingRunStatus.VALID,
                )
                for i in (1, 2, 3)
            ]
        )
        session.flush()
        session.add_all(
            [
                snapshot(1, "600", NOW - timedelta(days=20)),
                snapshot(2, "550", NOW - timedelta(days=10)),
                snapshot(3, "400", NOW - timedelta(days=100)),
            ]
        )
    with database.session() as session:
        events = detect_price_alerts(
            session,
            listing_id=1,
            run_id=4,
            tracked_search_id=None,
            previous=snapshot(4, "550", NOW - timedelta(days=1)),
            current=snapshot(5, "500", NOW),
            config=config(
                notify_on_30d_low=True, notify_on_90d_low=True, notify_on_all_time_low=True
            ),
        )
    assert {e["event_type"] for e in events} == {"NEW_30D_LOW", "NEW_90D_LOW"}
    assert all("previous_min" in e["metadata"] for e in events)


def test_equal_historical_minimum_is_not_a_new_low(database):
    with database.transaction() as session:
        session.add(
            ListingRecord(
                id=1,
                wallapop_item_id="1",
                first_seen_at=NOW,
                last_seen_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(TrackedSearchRecord(id=1, query="x", created_at=NOW, updated_at=NOW))
        session.flush()
        session.add(
            TrackingRunRecord(
                id=1,
                tracked_search_id=1,
                started_at=NOW,
                finished_at=NOW,
                status=TrackingRunStatus.VALID,
            )
        )
        session.flush()
        session.add(snapshot(1, "500", NOW - timedelta(days=10)))
    with database.session() as session:
        assert (
            detect_price_alerts(
                session,
                listing_id=1,
                run_id=2,
                tracked_search_id=None,
                previous=snapshot(2, "500", NOW - timedelta(days=1)),
                current=snapshot(3, "500", NOW),
                config=config(notify_on_30d_low=True, notify_on_all_time_low=True),
            )
            == []
        )


def test_target_price_rearms_after_price_rises_above_target(database):
    values = [("550", "480"), ("480", "470"), ("470", "520"), ("520", "490")]
    events = []
    with database.session() as session:
        for number, (old, new) in enumerate(values, 1):
            events.extend(
                detect_price_alerts(
                    session,
                    listing_id=1,
                    run_id=number,
                    tracked_search_id=None,
                    previous=snapshot(number, old, NOW - timedelta(minutes=number)),
                    current=snapshot(number + 10, new, NOW),
                    config=config(target_price=Decimal("500")),
                )
            )
    assert [event["event_type"] for event in events] == [
        "TARGET_PRICE_REACHED",
        "TARGET_PRICE_REACHED",
    ]


def test_price_metadata_explains_each_advanced_alert(database):
    with database.transaction() as session:
        session.add_all(
            [
                ListingRecord(
                    id=1,
                    wallapop_item_id="1",
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                TrackedSearchRecord(id=1, query="x", created_at=NOW, updated_at=NOW),
                *[
                    TrackingRunRecord(
                        id=i,
                        tracked_search_id=1,
                        started_at=NOW,
                        finished_at=NOW,
                        status=TrackingRunStatus.VALID,
                    )
                    for i in (1, 2, 3)
                ],
            ]
        )
        session.flush()
        session.add(snapshot(1, "100", NOW - timedelta(days=1)))
    with database.session() as session:
        events = detect_price_alerts(
            session,
            listing_id=1,
            run_id=2,
            tracked_search_id=1,
            previous=snapshot(2, "100", NOW - timedelta(hours=1)),
            current=snapshot(3, "90", NOW),
            config=config(
                target_price=Decimal("95"),
                percentage_drop_threshold=Decimal("10"),
                notify_on_30d_low=True,
                notify_on_90d_low=True,
                notify_on_all_time_low=True,
            ),
        )
    by_type = {event["event_type"]: event["metadata"] for event in events}
    assert by_type["TARGET_PRICE_REACHED"]["previous_price"] == Decimal("100")
    assert by_type["TARGET_PRICE_REACHED"]["current_price"] == Decimal("90")
    assert by_type["TARGET_PRICE_REACHED"]["threshold"] == Decimal("95")
    assert by_type["PERCENTAGE_DROP"]["drop_percentage"] == Decimal("10")


def test_historical_low_window_boundaries_are_deterministic(database):
    with database.transaction() as session:
        session.add_all(
            [
                ListingRecord(
                    id=1,
                    wallapop_item_id="1",
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                TrackedSearchRecord(id=1, query="x", created_at=NOW, updated_at=NOW),
                *[
                    TrackingRunRecord(
                        id=i,
                        tracked_search_id=1,
                        started_at=NOW,
                        finished_at=NOW,
                        status=TrackingRunStatus.VALID,
                    )
                    for i in (1, 2, 3)
                ],
            ]
        )
        session.flush()
        session.add_all(
            [
                snapshot(1, "400", NOW - timedelta(days=80)),
                snapshot(2, "450", NOW - timedelta(days=30)),
                snapshot(3, "550", NOW - timedelta(days=10)),
            ]
        )
    with database.session() as session:
        events = detect_price_alerts(
            session,
            listing_id=1,
            run_id=2,
            tracked_search_id=1,
            previous=snapshot(4, "550", NOW - timedelta(hours=1)),
            current=snapshot(5, "350", NOW),
            config=config(notify_on_30d_low=True, notify_on_90d_low=True),
        )
    assert {event["event_type"] for event in events} == {"NEW_30D_LOW", "NEW_90D_LOW"}
    assert all(event["metadata"]["window_days"] in (30, 90) for event in events)
