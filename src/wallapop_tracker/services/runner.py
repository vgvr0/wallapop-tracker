"""Reusable application service for running one tracked profile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ..client import WallapopClient
from ..providers.search import SearchProvider, WallapopSearchProvider
from ..storage.database import Database
from ..storage.models import TrackingRunStatus
from ..storage.repositories import TrackedProfileRepository, TrackedSearchRepository
from .search_tracker import SearchTracker, SearchTrackingResult
from .tracker import ProfileTracker, TrackingResult

type ClientFactory = Callable[[], WallapopClient]
type TrackerFactory = Callable[[WallapopClient, Database], ProfileTracker]


@dataclass(frozen=True)
class ProfileTrackingResult:
    """Outcome of a tracked-profile execution."""

    alias: str
    attempted_at: datetime
    status: TrackingRunStatus
    tracking_result: TrackingResult | None = None
    error: str | None = None


class ProfileTrackingRunner:
    """Run a configured profile and update its scheduling metadata."""

    def __init__(
        self,
        database: Database,
        *,
        client_factory: ClientFactory = WallapopClient,
        tracker_factory: TrackerFactory = ProfileTracker,
    ) -> None:
        self.database = database
        self.client_factory = client_factory
        self.tracker_factory = tracker_factory

    async def run(self, alias: str, *, now: datetime | None = None) -> ProfileTrackingResult:
        attempted_at = now or datetime.now(UTC)
        with self.database.session() as session:
            tracked = TrackedProfileRepository(session).get_by_alias(alias)
            if tracked is None:
                raise ValueError(f"Unknown alias: {alias}")
            profile_url = tracked.profile_url

        try:
            async with self.client_factory() as client:
                result = await self.tracker_factory(client, self.database).track_profile(
                    profile_url
                )
        except Exception as exc:
            self._record_run(alias, attempted_at, TrackingRunStatus.FAILED)
            return ProfileTrackingResult(
                alias=alias,
                attempted_at=attempted_at,
                status=TrackingRunStatus.FAILED,
                error=str(exc),
            )

        self._record_run(alias, attempted_at, result.status, result.profile_id)
        return ProfileTrackingResult(
            alias=alias,
            attempted_at=attempted_at,
            status=result.status,
            tracking_result=result,
            error=result.error,
        )

    def _record_run(
        self,
        alias: str,
        attempted_at: datetime,
        status: TrackingRunStatus,
        profile_id: int | None = None,
    ) -> None:
        with self.database.transaction() as session:
            repository = TrackedProfileRepository(session)
            repository.update_last_run(alias, attempted_at, status)
            if profile_id is not None:
                repository.attach_profile(alias, profile_id)


type SearchProviderFactory = Callable[[WallapopClient], SearchProvider]
type SearchTrackerFactory = Callable[[SearchProvider, Database], SearchTracker]


class SearchTrackingRunner:
    """Run one tracked search with the same client lifecycle as profiles."""

    def __init__(
        self,
        database: Database,
        *,
        client_factory: ClientFactory = WallapopClient,
        tracker_factory: SearchTrackerFactory = SearchTracker,
        provider_factory: SearchProviderFactory = WallapopSearchProvider,
    ) -> None:
        self.database = database
        self.client_factory = client_factory
        self.tracker_factory = tracker_factory
        self.provider_factory = provider_factory

    async def run(self, search_id: int) -> SearchTrackingResult:
        with self.database.session() as session:
            search = TrackedSearchRepository(session).get(search_id)
            if search is None:
                raise ValueError(f"Unknown search: {search_id}")
            if not search.enabled:
                return SearchTrackingResult(search_id, None, None, 0)
        try:
            async with self.client_factory() as client:
                provider = self.provider_factory(client)
                return await self.tracker_factory(provider, self.database).track_search(search_id)
        except Exception as exc:
            return SearchTrackingResult(
                search_id, None, TrackingRunStatus.FAILED, 0, error=str(exc)
            )
