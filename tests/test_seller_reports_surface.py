"""SQLite/API/CLI surface coverage for seller reports history."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from wallapop_tracker.api import create_app
from wallapop_tracker.cli import app
from wallapop_tracker.models import Profile, ProfileStats
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import ProfileSnapshotRecord
from wallapop_tracker.storage.repositories import (
    ProfileRepository,
    SnapshotRepository,
    TrackedProfileRepository,
    TrackingRunRepository,
)


def _seed(database: Database, reports_received: int | None) -> tuple[int, str]:
    now = datetime.now(UTC)
    with database.transaction() as session:
        profile = ProfileRepository(session).get_or_create_profile(
            Profile(user_id="surface-user", url="https://es.wallapop.com/user/surface-user"),
            observed_at=now,
        )
        tracked = TrackedProfileRepository(session).create(
            "https://es.wallapop.com/user/surface-user", "surface-user", "surface"
        )
        TrackedProfileRepository(session).attach_profile(tracked.alias, profile.id)
        runs = TrackingRunRepository(session)
        run = runs.start_tracking_run(profile.id, started_at=now)
        runs.mark_valid(run.id, items_ok=True)
        SnapshotRepository(session).save_profile_snapshot(
            profile.id, run.id, ProfileStats(reports_received=reports_received), observed_at=now
        )
        return profile.id, tracked.alias


def test_sqlite_persists_zero_and_null(database):
    profile_id, _ = _seed(database, 0)
    with database.session() as session:
        snapshot = session.scalar(select(ProfileSnapshotRecord))
        assert snapshot.profile_id == profile_id
        assert snapshot.reports_received == 0


def test_api_and_cli_expose_unknown(tmp_path, monkeypatch):
    database = Database(f"sqlite:///{tmp_path / 'surface.db'}")
    database.create_all()
    _seed(database, None)
    with TestClient(create_app(database)) as client:
        assert client.get("/api/v1/profiles/1").json()["reports_received"] is None
        assert client.get("/api/v1/profiles/1/history").json()[0]["reports_received"] is None
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{tmp_path / 'surface.db'}")
    result = CliRunner().invoke(app, ["profile", "show", "surface"])
    assert result.exit_code == 0
    assert "Reports received: unknown" in result.output
    database.close()
