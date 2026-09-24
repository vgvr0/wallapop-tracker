"""PostgreSQL coverage for the seller reports history feature."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from wallapop_tracker.models import Profile, ProfileStats
from wallapop_tracker.reporting.queries import get_profile_metrics_history
from wallapop_tracker.services.diff import DiffService
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import ProfileSnapshotRecord
from wallapop_tracker.storage.repositories import (
    ProfileRepository,
    SnapshotRepository,
    TrackingRunRepository,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_database():
    url = os.getenv("WALLAPOP_TRACKER_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("WALLAPOP_TRACKER_TEST_POSTGRES_URL is not configured")
    database = Database(url)
    try:
        with database.engine.connect() as connection:
            connection.execute(select(1))
        yield database
    except Exception as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    finally:
        database.close()


def test_reports_received_values_history_and_change(postgres_database):
    now = datetime.now(UTC)
    suffix = uuid4().hex
    with postgres_database.transaction() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(
                user_id=f"reports-{suffix}", url=f"https://es.wallapop.com/user/reports-{suffix}"
            ),
            observed_at=now,
        )
        runs = TrackingRunRepository(session)
        snapshots = SnapshotRepository(session)
        times = [now + timedelta(minutes=index) for index in range(4)]
        run_0 = runs.start_tracking_run(profile.id, started_at=times[0])
        runs.mark_valid(run_0.id, items_ok=True)
        unknown = snapshots.save_profile_snapshot(
            profile.id, run_0.id, ProfileStats(reports_received=None), observed_at=times[0]
        )
        run_1 = runs.start_tracking_run(profile.id, started_at=times[1])
        runs.mark_valid(run_1.id, items_ok=True)
        first = snapshots.save_profile_snapshot(
            profile.id, run_1.id, ProfileStats(reports_received=0), observed_at=times[1]
        )
        run_2 = runs.start_tracking_run(profile.id, started_at=times[2])
        runs.mark_valid(run_2.id, items_ok=True)
        second = snapshots.save_profile_snapshot(
            profile.id, run_2.id, ProfileStats(reports_received=5), observed_at=times[2]
        )
        run_3 = runs.start_tracking_run(profile.id, started_at=times[3])
        runs.mark_valid(run_3.id, items_ok=True)
        third = snapshots.save_profile_snapshot(
            profile.id, run_3.id, ProfileStats(reports_received=8), observed_at=times[3]
        )
        assert unknown is not None and unknown.reports_received is None
        assert first is not None and first.reports_received == 0
        assert second is not None and second.reports_received == 5
        assert third is not None and third.reports_received == 8
        assert (
            session.scalar(
                select(ProfileSnapshotRecord.reports_received).where(
                    ProfileSnapshotRecord.id == first.id
                )
            )
            == 0
        )
        history = get_profile_metrics_history(session, profile.id)
        assert [point.reports_received for point in history] == [None, 0, 5, 8]
        changes = DiffService(session).compare_runs(run_2.id, run_3.id)
        report_change = next(
            change for change in changes if change.change_type == "PROFILE_REPORTS_CHANGED"
        )
        assert (report_change.old_value, report_change.new_value, report_change.delta) == (5, 8, 3)
