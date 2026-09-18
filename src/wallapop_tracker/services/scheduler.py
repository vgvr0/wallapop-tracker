"""Bounded-concurrency scheduler for enabled tracking sources."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..observability import get_metrics
from ..storage.database import Database
from ..storage.models import TrackingRunStatus
from ..storage.repositories import (
    TrackedListingRepository,
    TrackedProfileRepository,
    TrackedSearchRepository,
)
from .listing_tracker import ListingTrackingResult
from .notifications import NotificationService
from .runner import (
    ListingTrackingRunner,
    ProfileTrackingResult,
    ProfileTrackingRunner,
    SearchTrackingRunner,
)
from .search_tracker import SearchTrackingResult

Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulerResult:
    """Summary and individual outcomes for one scheduler poll."""

    evaluated: int
    executed: int
    succeeded: int
    failed: int
    results: tuple[ProfileTrackingResult | SearchTrackingResult | ListingTrackingResult, ...]
    profile_results: tuple[ProfileTrackingResult, ...] = ()
    search_results: tuple[SearchTrackingResult, ...] = ()
    listing_results: tuple[ListingTrackingResult, ...] = ()
    max_concurrency: int = 4


class TrackingScheduler:
    """Poll enabled profiles and run those whose interval has elapsed."""

    def __init__(
        self,
        database: Database,
        interval: timedelta,
        *,
        poll_seconds: float = 60.0,
        runner: ProfileTrackingRunner | None = None,
        search_runner: SearchTrackingRunner | None = None,
        listing_runner: ListingTrackingRunner | None = None,
        notification_service: NotificationService | None = None,
        clock: Clock | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must not be negative")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.database = database
        self.interval = interval
        self.poll_seconds = poll_seconds
        self.runner = runner or ProfileTrackingRunner(database)
        self.search_runner = search_runner or SearchTrackingRunner(database)
        self.listing_runner = listing_runner or ListingTrackingRunner(database)
        self.notification_service = notification_service or NotificationService(database)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_concurrency = max_concurrency

    async def run_once(self, *, now: datetime | None = None) -> SchedulerResult:
        metrics = get_metrics()
        metrics.scheduler_polls_total.inc()
        current = _as_utc(now or self.clock())
        with self.database.session() as session:
            records = TrackedProfileRepository(session).list_enabled()
            searches = TrackedSearchRepository(session).list_enabled()
            listings = TrackedListingRepository(session).list_enabled()
        due = [record for record in records if _is_due(record.last_run_at, current, self.interval)]
        due_searches = [
            search
            for search in searches
            if _is_due(
                search.last_run_at,
                current,
                timedelta(seconds=search.interval_seconds),
            )
        ]
        due_listings = [
            listing
            for listing in listings
            if _is_due(listing.last_run_at, current, timedelta(seconds=listing.interval_seconds))
        ]
        total_jobs = len(due) + len(due_searches) + len(due_listings)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        results: list[
            ProfileTrackingResult | SearchTrackingResult | ListingTrackingResult | None
        ] = [None] * total_jobs
        profile_results: list[ProfileTrackingResult] = []
        search_results: list[SearchTrackingResult] = []
        listing_results: list[ListingTrackingResult] = []

        async def run_profile(index: int, alias: str) -> None:
            async with semaphore:
                metrics.scheduler_active_jobs.inc()
                try:
                    result = await self.runner.run(alias, now=current)
                except Exception as exc:
                    result = ProfileTrackingResult(
                        alias=alias,
                        attempted_at=current,
                        status=TrackingRunStatus.FAILED,
                        error=str(exc),
                    )
                finally:
                    metrics.scheduler_active_jobs.dec()
                results[index] = result

        async def run_search(index: int, search_id: int) -> None:
            async with semaphore:
                metrics.scheduler_active_jobs.inc()
                try:
                    result = await self.search_runner.run(search_id)
                except Exception as exc:
                    result = SearchTrackingResult(
                        search_id, None, TrackingRunStatus.FAILED, 0, error=str(exc)
                    )
                finally:
                    metrics.scheduler_active_jobs.dec()
                results[index] = result

        async def run_listing(index: int, listing_id: int) -> None:
            async with semaphore:
                metrics.scheduler_active_jobs.inc()
                try:
                    result = await self.listing_runner.run(listing_id)
                except Exception as exc:
                    result = ListingTrackingResult(
                        listing_id, None, TrackingRunStatus.FAILED, error=str(exc)
                    )
                finally:
                    metrics.scheduler_active_jobs.dec()
                results[index] = result

        async with asyncio.TaskGroup() as task_group:
            for index, record in enumerate(due):
                task_group.create_task(run_profile(index, record.alias))
            search_offset = len(due)
            for offset, search in enumerate(due_searches):
                task_group.create_task(run_search(search_offset + offset, search.id))
            listing_offset = search_offset + len(due_searches)
            for offset, listing in enumerate(due_listings):
                task_group.create_task(run_listing(listing_offset + offset, listing.id))

        ordered_results = tuple(result for result in results if result is not None)
        profile_results = [
            result for result in ordered_results if isinstance(result, ProfileTrackingResult)
        ]
        search_results = [
            result for result in ordered_results if isinstance(result, SearchTrackingResult)
        ]
        listing_results = [
            result for result in ordered_results if isinstance(result, ListingTrackingResult)
        ]
        failed = sum(result.status == TrackingRunStatus.FAILED for result in ordered_results)
        for result in ordered_results:
            source = (
                "profile"
                if isinstance(result, ProfileTrackingResult)
                else "search"
                if isinstance(result, SearchTrackingResult)
                else "listing"
            )
            metrics.scheduler_jobs_executed_total.labels(source).inc()
            if result.status == TrackingRunStatus.FAILED:
                metrics.scheduler_jobs_failed_total.labels(source).inc()
        for result in search_results:
            if result.alerts:
                try:
                    self.notification_service.enqueue_events(
                        alert.event_id for alert in result.alerts
                    )
                except Exception:
                    logger.exception("notification_enqueue_failed", extra={"source": "search"})
        for listing_result in listing_results:
            if listing_result.alerts:
                try:
                    self.notification_service.enqueue_events(
                        alert.event_id for alert in listing_result.alerts
                    )
                except Exception:
                    logger.exception("notification_enqueue_failed", extra={"source": "listing"})
        try:
            await self.notification_service.deliver_pending()
        except Exception:
            logger.exception("notification_delivery_cycle_failed")
        return SchedulerResult(
            evaluated=len(records) + len(searches) + len(listings),
            executed=len(ordered_results),
            succeeded=len(ordered_results) - failed,
            failed=failed,
            results=ordered_results,
            profile_results=tuple(profile_results),
            search_results=tuple(search_results),
            listing_results=tuple(listing_results),
            max_concurrency=self.max_concurrency,
        )

    async def run_forever(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.poll_seconds)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_due(last_run_at: datetime | None, now: datetime, interval: timedelta) -> bool:
    return last_run_at is None or _as_utc(last_run_at) + interval <= now
