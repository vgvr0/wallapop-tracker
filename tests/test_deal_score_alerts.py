import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from wallapop_tracker.storage.models import (
    DealScoreSnapshotRecord,
    ListingRecord,
    TrackedSearchRecord,
    TrackingEventRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import TrackingEventRepository, latest_deal_score


@pytest.mark.asyncio
async def test_deal_score_crossing_rearming_survives_real_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import func

    from wallapop_tracker.models import Listing
    from wallapop_tracker.services.search_tracker import SearchTracker
    from wallapop_tracker.storage.database import Database
    from wallapop_tracker.storage.repositories import TrackedSearchRepository

    path = tmp_path / "restart.db"
    scores = iter(
        [Decimal("70"), Decimal("85"), Decimal("85"), Decimal("75"), Decimal("82"), Decimal("82")]
    )
    monkeypatch.setattr(
        "wallapop_tracker.services.search_tracker.DealScoringService.score_listing",
        lambda self, listing_id, search_id: SimpleNamespace(score=next(scores)),
    )

    class Provider:
        async def search(self, request):
            return [
                Listing(
                    item_id="item-1",
                    user_id="seller",
                    title="Camera",
                    price=Decimal("100"),
                    currency="EUR",
                    url="https://example/item-1",
                )
            ]

    def database():
        db = Database(f"sqlite:///{path}")
        db.create_all()
        return db

    db = database()
    with db.transaction() as session:
        search_id = (
            TrackedSearchRepository(session).create("camera", deal_score_threshold=Decimal("80")).id
        )
    for expected in (0, 1):
        result = await SearchTracker(Provider(), db).track_search(search_id)
        assert (
            sum(alert.type.value == "DEAL_SCORE_THRESHOLD" for alert in result.alerts) == expected
        )
    db.close()
    db = database()
    assert (
        sum(
            alert.type.value == "DEAL_SCORE_THRESHOLD"
            for alert in (await SearchTracker(Provider(), db).track_search(search_id)).alerts
        )
        == 0
    )
    assert (
        sum(
            alert.type.value == "DEAL_SCORE_THRESHOLD"
            for alert in (await SearchTracker(Provider(), db).track_search(search_id)).alerts
        )
        == 0
    )
    assert (
        sum(
            alert.type.value == "DEAL_SCORE_THRESHOLD"
            for alert in (await SearchTracker(Provider(), db).track_search(search_id)).alerts
        )
        == 1
    )
    repeat = await SearchTracker(Provider(), db).track_search(search_id)
    assert sum(alert.type.value == "DEAL_SCORE_THRESHOLD" for alert in repeat.alerts) == 0
    with db.session() as session:
        assert (
            session.scalar(
                select(func.count()).where(TrackingEventRecord.event_type == "DEAL_SCORE_THRESHOLD")
            )
            == 2
        )
        assert session.scalar(select(func.count()).select_from(DealScoreSnapshotRecord)) == 6
    db.close()


@pytest.mark.parametrize(
    "event_type",
    [
        "TARGET_PRICE_REACHED",
        "PERCENTAGE_DROP",
        "NEW_30D_LOW",
        "NEW_90D_LOW",
        "NEW_ALL_TIME_LOW",
        "DEAL_SCORE_THRESHOLD",
    ],
)
def test_tracking_event_repository_is_idempotent(database, event_type):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with database.transaction() as session:
        session.add(
            ListingRecord(
                id=1,
                wallapop_item_id="1",
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(TrackedSearchRecord(id=10, query="a", created_at=now, updated_at=now))
        session.add(
            TrackingRunRecord(
                id=1,
                tracked_search_id=10,
                started_at=now,
                finished_at=now,
                status=TrackingRunStatus.VALID,
            )
        )
        session.flush()
        repo = TrackingEventRepository(session)
        first, created = repo.create_once(
            event_type=event_type,
            idempotency_key="same-transition",
            listing_id=1,
            tracking_run_id=1,
            tracked_search_id=10,
            old_price=Decimal("100"),
            new_price=Decimal("80"),
            created_at=now,
            metadata_json=json.dumps({"event": event_type}),
        )
        second, duplicate = repo.create_once(
            event_type=event_type,
            idempotency_key="same-transition",
            listing_id=1,
            tracking_run_id=1,
            tracked_search_id=10,
            old_price=Decimal("100"),
            new_price=Decimal("80"),
            created_at=now,
            metadata_json=json.dumps({"event": event_type}),
        )
        assert created is True and duplicate is False
        assert first.id == second.id
        assert session.query(TrackingEventRecord).count() == 1


def test_score_history_is_contextual_and_restart_safe(database):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with database.transaction() as session:
        session.add(
            ListingRecord(
                id=1,
                wallapop_item_id="1",
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(TrackedSearchRecord(id=10, query="a", created_at=now, updated_at=now))
        session.add(TrackedSearchRecord(id=20, query="b", created_at=now, updated_at=now))
        session.flush()
        session.add_all(
            [
                DealScoreSnapshotRecord(
                    listing_id=1, tracked_search_id=10, score=Decimal("79"), computed_at=now
                ),
                DealScoreSnapshotRecord(
                    listing_id=1,
                    tracked_search_id=10,
                    score=Decimal("80"),
                    computed_at=now + timedelta(minutes=1),
                ),
                DealScoreSnapshotRecord(
                    listing_id=1, tracked_search_id=20, score=Decimal("70"), computed_at=now
                ),
            ]
        )
    with database.session() as session:
        latest_a = latest_deal_score(session, 1, 10)
        latest_b = latest_deal_score(session, 1, 20)
        assert latest_a is not None and latest_a.score == Decimal("80")
        assert latest_b is not None and latest_b.score == Decimal("70")


def test_score_snapshot_query_uses_latest_observation(database):
    now = datetime(2026, 2, 1, tzinfo=UTC)
    with database.transaction() as session:
        session.add(
            ListingRecord(
                id=1,
                wallapop_item_id="1",
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(TrackedSearchRecord(id=3, query="a", created_at=now, updated_at=now))
        session.flush()
        session.add(
            DealScoreSnapshotRecord(
                listing_id=1, tracked_search_id=3, score=Decimal("90"), computed_at=now
            )
        )
    with database.session() as session:
        row = session.scalar(
            select(DealScoreSnapshotRecord).where(
                DealScoreSnapshotRecord.listing_id == 1,
                DealScoreSnapshotRecord.tracked_search_id == 3,
            )
        )
        assert row is not None
        assert row.score == Decimal("90")
