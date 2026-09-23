import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.services.runner import ProfileTrackingResult
from wallapop_tracker.services.scheduler import TrackingScheduler
from wallapop_tracker.services.search_tracker import SearchTrackingResult
from wallapop_tracker.services.tracker import TrackingResult
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackingRunStatus
from wallapop_tracker.storage.repositories import TrackedProfileRepository, TrackedSearchRepository

runner = CliRunner()
NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


class FakeRunner:
    def __init__(self, failures=()):
        self.aliases = []
        self.failures = set(failures)

    async def run(self, alias, *, now):
        self.aliases.append(alias)
        if alias in self.failures:
            raise RuntimeError(f"failed: {alias}")
        return ProfileTrackingResult(alias, now, TrackingRunStatus.VALID)


class FakeSearchRunner:
    def __init__(self):
        self.search_ids = []

    async def run(self, search_id):
        self.search_ids.append(search_id)
        return SearchTrackingResult(search_id, 1, TrackingRunStatus.VALID, 1, 1, 1)


def add_profiles(database, *aliases):
    with database.transaction() as session:
        repository = TrackedProfileRepository(session)
        for alias in aliases:
            repository.create(f"https://es.wallapop.com/user/{alias}", alias, alias)


def set_last_run(database, alias, at):
    with database.transaction() as session:
        TrackedProfileRepository(session).update_last_run(alias, at, TrackingRunStatus.VALID)


@pytest.mark.asyncio
async def test_scheduler_coexists_with_due_tracked_search(database):
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create("iphone", interval_seconds=60)
        search_id = search.id
    fake_search = FakeSearchRunner()
    scheduler = TrackingScheduler(
        database,
        timedelta(hours=24),
        runner=FakeRunner(),
        search_runner=fake_search,
        clock=lambda: NOW,
    )

    result = await scheduler.run_once()

    assert result.evaluated == 1
    assert result.executed == 1
    assert fake_search.search_ids == [search_id]


@pytest.mark.asyncio
async def test_scheduler_runs_profiles_without_last_run_and_overdue(database):
    add_profiles(database, "never", "overdue", "current")
    set_last_run(database, "overdue", NOW - timedelta(hours=25))
    set_last_run(database, "current", NOW - timedelta(hours=23))
    fake = FakeRunner()
    scheduler = TrackingScheduler(database, timedelta(hours=24), runner=fake, clock=lambda: NOW)

    result = await scheduler.run_once()

    assert fake.aliases == ["never", "overdue"]
    assert result.evaluated == 3
    assert result.executed == 2
    assert result.succeeded == 2
    assert result.failed == 0


@pytest.mark.asyncio
async def test_scheduler_skips_disabled_and_continues_after_failure(database):
    add_profiles(database, "broken", "disabled", "next")
    with database.transaction() as session:
        TrackedProfileRepository(session).disable("disabled")
    fake = FakeRunner(failures=("broken",))
    scheduler = TrackingScheduler(database, timedelta(hours=24), runner=fake, clock=lambda: NOW)

    result = await scheduler.run_once()

    assert fake.aliases == ["broken", "next"]
    assert result.executed == 2
    assert result.succeeded == 1
    assert result.failed == 1


@pytest.mark.asyncio
async def test_scheduler_updates_last_run_after_real_runner_execution(database):
    add_profiles(database, "andrey")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeTracker:
        def __init__(self, client, database):
            pass

        async def track_profile(self, url):
            return TrackingResult(1, TrackingRunStatus.VALID, None, 2, 1)

    from wallapop_tracker.services.runner import ProfileTrackingRunner

    service = ProfileTrackingRunner(
        database, client_factory=FakeClient, tracker_factory=FakeTracker
    )
    scheduler = TrackingScheduler(database, timedelta(hours=24), runner=service, clock=lambda: NOW)

    await scheduler.run_once()

    with database.session() as session:
        record = TrackedProfileRepository(session).get_by_alias("andrey")
        assert record is not None
        assert record.last_run_at is not None
        assert record.last_run_at.replace(tzinfo=UTC) == NOW


@pytest.mark.asyncio
async def test_run_forever_uses_injected_sleep_without_real_wait(database, monkeypatch):
    add_profiles(database, "andrey")
    fake = FakeRunner()
    scheduler = TrackingScheduler(database, timedelta(hours=24), runner=fake, poll_seconds=30)

    async def stop_after_sleep(seconds):
        assert seconds == 30
        raise RuntimeError("stop test loop")

    monkeypatch.setattr(asyncio, "sleep", stop_after_sleep)
    with pytest.raises(RuntimeError, match="stop test loop"):
        await scheduler.run_forever()


def test_cli_schedule_once(tmp_path, monkeypatch):
    path = tmp_path / "schedule.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    with database.transaction() as session:
        TrackedProfileRepository(session).create(
            "https://es.wallapop.com/user/andrey", "user-a", "andrey"
        )
    database.close()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeTracker:
        def __init__(self, client, database):
            pass

        async def track_profile(self, url):
            return TrackingResult(1, TrackingRunStatus.VALID, None, 2, 1)

    monkeypatch.setattr(cli, "WallapopClient", FakeClient)
    monkeypatch.setattr(cli, "ProfileTracker", FakeTracker)
    result = runner.invoke(cli.app, ["schedule", "--once", "--interval-hours", "24"])

    assert result.exit_code == 0
    assert "evaluated: 1 executed: 1 succeeded: 1 failed: 0" in result.output
