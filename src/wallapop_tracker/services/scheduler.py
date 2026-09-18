"""Sequential scheduler for enabled tracked profiles."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("interval must be positive")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must not be negative")
        self.database = database
        self.interval = interval
        self.poll_seconds = poll_seconds
        self.runner = runner or ProfileTrackingRunner(database)
        self.search_runner = search_runner or SearchTrackingRunner(database)
        self.listing_runner = listing_runner or ListingTrackingRunner(database)
        self.notification_service = notification_service or NotificationService(database)
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self, *, now: datetime | None = None) -> SchedulerResult:
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
        results: list[ProfileTrackingResult | SearchTrackingResult | ListingTrackingResult] = []
        profile_results: list[ProfileTrackingResult] = []
        search_results: list[SearchTrackingResult] = []
        listing_results: list[ListingTrackingResult] = []
        for record in due:
            try:
                profile_result = await self.runner.run(record.alias, now=current)
            except Exception as exc:
                profile_result = ProfileTrackingResult(
                    alias=record.alias,
                    attempted_at=current,
                    status=TrackingRunStatus.FAILED,
                    error=str(exc),
                )
            results.append(profile_result)
            profile_results.append(profile_result)
        for search in due_searches:
            try:
                current_search_result = await self.search_runner.run(search.id)
            except Exception as exc:
                current_search_result = SearchTrackingResult(
                    search.id, None, TrackingRunStatus.FAILED, 0, error=str(exc)
                )
            results.append(current_search_result)
            search_results.append(current_search_result)
        for listing in due_listings:
            try:
                current_listing_result = await self.listing_runner.run(listing.id)
            except Exception as exc:
                current_listing_result = ListingTrackingResult(
                    listing.id, None, TrackingRunStatus.FAILED, error=str(exc)
                )
            results.append(current_listing_result)
            listing_results.append(current_listing_result)
        failed = sum(result.status == TrackingRunStatus.FAILED for result in results)
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
            executed=len(results),
            succeeded=len(results) - failed,
            failed=failed,
            results=tuple(results),
            profile_results=tuple(profile_results),
            search_results=tuple(search_results),
            listing_results=tuple(listing_results),
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
