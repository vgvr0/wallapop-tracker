"""Sequential scheduler for enabled tracked profiles."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..storage.database import Database
from ..storage.models import TrackingRunStatus
from ..storage.repositories import TrackedProfileRepository
from .runner import ProfileTrackingResult, ProfileTrackingRunner

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class SchedulerResult:
    """Summary and individual outcomes for one scheduler poll."""

    evaluated: int
    executed: int
    succeeded: int
    failed: int
    results: tuple[ProfileTrackingResult, ...]


class TrackingScheduler:
    """Poll enabled profiles and run those whose interval has elapsed."""

    def __init__(
        self,
        database: Database,
        interval: timedelta,
        *,
        poll_seconds: float = 60.0,
        runner: ProfileTrackingRunner | None = None,
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
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self, *, now: datetime | None = None) -> SchedulerResult:
        current = _as_utc(now or self.clock())
        with self.database.session() as session:
            records = TrackedProfileRepository(session).list_enabled()
        due = [record for record in records if _is_due(record.last_run_at, current, self.interval)]
        results: list[ProfileTrackingResult] = []
        for record in due:
            try:
                result = await self.runner.run(record.alias, now=current)
            except Exception as exc:
                results.append(
                    ProfileTrackingResult(
                        alias=record.alias,
                        attempted_at=current,
                        status=TrackingRunStatus.FAILED,
                        error=str(exc),
                    )
                )
            else:
                results.append(result)
        failed = sum(result.status == TrackingRunStatus.FAILED for result in results)
        return SchedulerResult(
            evaluated=len(records),
            executed=len(results),
            succeeded=len(results) - failed,
            failed=failed,
            results=tuple(results),
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
