import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

import pytest

from wallapop_tracker.client import WallapopClient
from wallapop_tracker.models import Listing
from wallapop_tracker.services.listing_tracker import ListingTrackingResult
from wallapop_tracker.services.runner import ProfileTrackingResult
from wallapop_tracker.services.scheduler import TrackingScheduler
from wallapop_tracker.services.search_tracker import SearchTrackingResult
from wallapop_tracker.services.tracker import TrackingResult
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackingRunStatus
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    TrackedListingRepository,
    TrackedProfileRepository,
    TrackedSearchRepository,
)

NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def add_profiles(database, *aliases: str) -> None:
    with database.transaction() as session:
        repository = TrackedProfileRepository(session)
        for alias in aliases:
            repository.create(f"https://es.wallapop.com/user/{alias}", alias, alias)


class MeasuredProfileRunner:
    def __init__(self, delays: dict[str, float] | None = None, failures=()):
        self.delays = delays or {}
        self.failures = set(failures)
        self.active = 0
        self.peak = 0

    async def run(self, alias: str, *, now: datetime) -> ProfileTrackingResult:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delays.get(alias, 0.01))
            if alias in self.failures:
                raise RuntimeError(f"failed: {alias}")
            return ProfileTrackingResult(alias, now, TrackingRunStatus.VALID)
        finally:
            self.active -= 1


class StableSearchRunner:
    async def run(self, search_id: int) -> SearchTrackingResult:
        await asyncio.sleep(0.005)
        return SearchTrackingResult(search_id, 1, TrackingRunStatus.VALID, 1)


class StableListingRunner:
    async def run(self, listing_id: int) -> ListingTrackingResult:
        await asyncio.sleep(0.005)
        return ListingTrackingResult(listing_id, 1, TrackingRunStatus.VALID)


class FakeNotificationService:
    def __init__(self):
        self.deliveries = 0

    def enqueue_events(self, event_ids):
        list(event_ids)

    async def deliver_pending(self):
        self.deliveries += 1


@pytest.mark.asyncio
async def test_scheduler_enforces_global_concurrency_and_stable_order(database):
    aliases = tuple(f"profile-{index}" for index in range(10))
    add_profiles(database, *aliases)
    runner = MeasuredProfileRunner(
        {alias: (10 - index) * 0.001 for index, alias in enumerate(aliases)}
    )
    scheduler = TrackingScheduler(
        database,
        timedelta(hours=24),
        runner=runner,
        max_concurrency=4,
        clock=lambda: NOW,
    )

    result = await scheduler.run_once()

    assert runner.peak == 4
    assert runner.peak > 1
    assert [item.alias for item in result.profile_results] == list(aliases)
    assert result.max_concurrency == 4


@pytest.mark.asyncio
async def test_scheduler_max_concurrency_one_is_sequential(database):
    aliases = ("a", "b", "c")
    add_profiles(database, *aliases)
    runner = MeasuredProfileRunner()
    scheduler = TrackingScheduler(
        database, timedelta(hours=24), runner=runner, max_concurrency=1, clock=lambda: NOW
    )

    await scheduler.run_once()

    assert runner.peak == 1


@pytest.mark.asyncio
async def test_scheduler_isolates_errors_without_cancelling_siblings(database):
    add_profiles(database, "a", "b", "c")
    runner = MeasuredProfileRunner(failures=("b",))
    scheduler = TrackingScheduler(
        database, timedelta(hours=24), runner=runner, max_concurrency=3, clock=lambda: NOW
    )

    result = await scheduler.run_once()

    assert [item.status for item in result.profile_results] == [
        TrackingRunStatus.VALID,
        TrackingRunStatus.FAILED,
        TrackingRunStatus.VALID,
    ]
    assert result.failed == 1


@pytest.mark.asyncio
async def test_scheduler_mixes_all_sources_under_one_limit(database):
    add_profiles(database, "profile")
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create("phone")
        listing_record, _ = ListingRepository(session).get_or_create_global_listing(
            Listing(item_id="listing", user_id="seller", price=Decimal("10")), None
        )
        tracked_listing = TrackedListingRepository(session).create(listing_record.id, "listing")
        search_id = search.id
        listing_id = tracked_listing.id
    profile_runner = MeasuredProfileRunner()
    notifications = FakeNotificationService()
    scheduler = TrackingScheduler(
        database,
        timedelta(hours=24),
        runner=profile_runner,
        search_runner=StableSearchRunner(),
        listing_runner=StableListingRunner(),
        notification_service=notifications,
        max_concurrency=2,
        clock=lambda: NOW,
    )

    result = await scheduler.run_once()

    assert result.executed == 3
    assert result.profile_results[0].status == TrackingRunStatus.VALID
    assert result.search_results[0].search_id == search_id
    assert result.listing_results[0].tracked_listing_id == listing_id
    assert notifications.deliveries == 1


@pytest.mark.asyncio
async def test_real_runner_sessions_persist_concurrent_sqlite_runs(database):
    add_profiles(database, *(f"profile-{index}" for index in range(6)))

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class FakeTracker:
        def __init__(self, client, database):
            pass

        async def track_profile(self, url):
            await asyncio.sleep(0.005)
            return TrackingResult(1, TrackingRunStatus.VALID, None, 0, 0)

    from wallapop_tracker.services.runner import ProfileTrackingRunner

    runner = ProfileTrackingRunner(database, client_factory=FakeClient, tracker_factory=FakeTracker)
    scheduler = TrackingScheduler(
        database, timedelta(hours=24), runner=runner, max_concurrency=3, clock=lambda: NOW
    )

    result = await scheduler.run_once()

    assert result.failed == 0
    with database.session() as session:
        records = TrackedProfileRepository(session).list_all()
        assert len(records) == 6
        assert all(record.last_run_status == TrackingRunStatus.VALID for record in records)


@pytest.mark.asyncio
async def test_clients_share_rate_limiter(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("wallapop_tracker.client.asyncio.sleep", fake_sleep)
    first = WallapopClient(base_url="https://shared-rate-limit.test", min_interval=0.1)
    second = WallapopClient(base_url="https://shared-rate-limit.test", min_interval=0.1)
    await asyncio.gather(first._wait_for_rate_limit(), second._wait_for_rate_limit())

    assert any(delay >= 0.09 for delay in sleeps)


@pytest.mark.asyncio
async def test_retry_after_blocks_other_shared_clients():
    first = WallapopClient(base_url="https://retry-after-rate-limit.test", min_interval=0)
    second = WallapopClient(base_url="https://retry-after-rate-limit.test", min_interval=0)
    await first._rate_limiter.block(0.02)
    started = perf_counter()
    await second._wait_for_rate_limit()
    assert perf_counter() - started >= 0.015


@pytest.mark.asyncio
async def test_bounded_scheduler_reduces_fake_job_wall_time(database):
    aliases = tuple(f"timed-{index}" for index in range(10))
    add_profiles(database, *aliases)

    sequential_runner = MeasuredProfileRunner({alias: 0.01 for alias in aliases})
    sequential = TrackingScheduler(
        database,
        timedelta(hours=24),
        runner=sequential_runner,
        max_concurrency=1,
        clock=lambda: NOW,
    )
    started = perf_counter()
    await sequential.run_once()
    sequential_duration = perf_counter() - started

    with database.transaction() as session:
        for record in TrackedProfileRepository(session).list_all():
            record.last_run_at = None
    parallel_runner = MeasuredProfileRunner({alias: 0.01 for alias in aliases})
    parallel = TrackingScheduler(
        database, timedelta(hours=24), runner=parallel_runner, max_concurrency=4, clock=lambda: NOW
    )
    started = perf_counter()
    await parallel.run_once()
    parallel_duration = perf_counter() - started

    assert parallel_duration < sequential_duration
