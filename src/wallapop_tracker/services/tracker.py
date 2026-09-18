"""Application service joining Wallapop extraction and historical storage."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from wallapop_tracker.client import WallapopClient
from wallapop_tracker.models import Profile, ProfileStats, ReviewSummary
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    PresenceState,
    ProfileRecord,
    TrackingRunListingRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    ProfileRepository,
    SnapshotRepository,
    TrackingRunRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackingResult:
    """Small, stable result returned by :meth:`ProfileTracker.track_profile`."""

    run_id: int
    status: TrackingRunStatus
    profile_id: int | None
    items_fetched: int
    pages_fetched: int | None
    error: str | None = None


@dataclass
class _Capture:
    profile: Profile | None = None
    stats: ProfileStats | None = None
    reviews: ReviewSummary | None = None
    listings: list[Any] | None = None
    profile_ok: bool = False
    stats_ok: bool = False
    reviews_ok: bool = False
    items_ok: bool = False
    error: BaseException | None = None
    error_component: str | None = None


class ProfileTracker:
    """Fetch one profile and persist it according to the tracking invariants.

    ``session_factory`` may be a SQLAlchemy ``sessionmaker`` or a ``Database``.
    HTTP extraction happens before the short database transaction.
    """

    def __init__(
        self,
        client: WallapopClient,
        session_factory: sessionmaker[Session] | Database,
    ) -> None:
        self.client = client
        self.session_factory = (
            session_factory.session_factory
            if isinstance(session_factory, Database)
            else session_factory
        )

    async def track_profile(self, profile_url: str) -> TrackingResult:
        started_at = datetime.now(UTC)
        user_id = await self._resolve_id(profile_url)
        capture = await self._extract(profile_url, user_id)
        if capture.profile is not None:
            user_id = capture.profile.user_id

        with self.session_factory() as session:
            existing = ProfileRepository(session).get_profile_by_wallapop_id(user_id)
            profile_id = existing.id if existing is not None else None

        if capture.profile_ok and capture.stats_ok and capture.reviews_ok and capture.items_ok:
            try:
                return self._persist_valid(
                    capture, profile_url, profile_id, started_at, user_id
                )
            except Exception as exc:
                logger.exception("valid_capture_persistence_failed user_id=%s", user_id)
                return self._persist_terminal(
                    profile_url,
                    user_id,
                    profile_id or self._ensure_anchor(user_id, profile_url, started_at),
                    started_at,
                    capture,
                    TrackingRunStatus.FAILED,
                    exc,
                )

        status = TrackingRunStatus.PARTIAL if any(
            (capture.profile_ok, capture.stats_ok, capture.reviews_ok, capture.items_ok)
        ) else TrackingRunStatus.FAILED
        return self._persist_terminal(
            profile_url,
            user_id,
            profile_id or self._ensure_anchor(user_id, profile_url, started_at),
            started_at,
            capture,
            status,
            capture.error,
        )

    async def _resolve_id(self, profile_url: str) -> str:
        try:
            return await self.client.resolve_user_id(profile_url)
        except Exception:
            parsed = urlparse(profile_url)
            value = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            return re.sub(r"[^A-Za-z0-9_-]", "_", value) or "unknown-profile"

    async def _extract(self, profile_url: str, user_id: str) -> _Capture:
        del profile_url
        capture = _Capture()
        try:
            capture.profile = await self.client.get_profile(user_id)
            capture.profile_ok = True
        except Exception as exc:
            return self._failed_capture(capture, "profile", exc)
        try:
            capture.stats = await self.client.get_profile_stats(user_id)
            capture.stats_ok = True
        except Exception as exc:
            capture.error, capture.error_component = exc, "stats"
        try:
            capture.reviews = await self.client.get_review_summary(user_id)
            capture.reviews_ok = True
        except Exception as exc:
            if capture.error is None:
                capture.error, capture.error_component = exc, "reviews"
        try:
            capture.listings = await self.client.get_all_items(user_id)
            capture.items_ok = True
        except Exception as exc:
            if capture.error is None:
                capture.error, capture.error_component = exc, "items"
        return capture

    @staticmethod
    def _failed_capture(capture: _Capture, component: str, exc: BaseException) -> _Capture:
        capture.error, capture.error_component = exc, component
        return capture

    def _persist_terminal(
        self,
        profile_url: str,
        user_id: str,
        profile_id: int,
        started_at: datetime,
        capture: _Capture,
        status: TrackingRunStatus,
        error: BaseException | None,
    ) -> TrackingResult:
        with self.session_factory.begin() as session:
            run = TrackingRunRepository(session).start_profile_run(
                profile_id, started_at=started_at
            )
            TrackingRunRepository(session).finish_tracking_run(
                run.id,
                status=status,
                items_fetched=len(capture.listings or []),
                pages_fetched=None,
                profile_ok=capture.profile_ok,
                stats_ok=capture.stats_ok,
                reviews_ok=capture.reviews_ok,
                items_ok=capture.items_ok,
                error_type=type(error).__name__ if error else capture.error_component,
                error_message=str(error) if error else None,
            )
            return TrackingResult(
                run_id=run.id,
                status=status,
                profile_id=profile_id,
                items_fetched=len(capture.listings or []),
                pages_fetched=None,
                error=str(error) if error else None,
            )

    def _persist_valid(
        self,
        capture: _Capture,
        profile_url: str,
        profile_id: int | None,
        started_at: datetime,
        user_id: str,
    ) -> TrackingResult:
        assert capture.profile is not None
        assert capture.stats is not None
        assert capture.reviews is not None
        listings = capture.listings or []
        with self.session_factory.begin() as session:
            if profile_id is None:
                profile_id = self._identity_profile(
                    session, user_id, profile_url, started_at
                ).id
            runs = TrackingRunRepository(session)
            run = runs.start_profile_run(profile_id, started_at=started_at)
            runs.mark_valid(
                run.id,
                items_fetched=len(listings),
                profile_ok=True,
                stats_ok=True,
                reviews_ok=True,
                items_ok=True,
            )
            profiles = ProfileRepository(session)
            profile_record = profiles.get_or_create_profile(
                capture.profile,
                observed_at=started_at,
                tracking_run_id=run.id,
            )
            self._warn_on_discrepancy(capture.stats, capture.reviews, user_id)
            SnapshotRepository(session).save_profile_snapshot(
                profile_record.id, run.id, capture.stats, capture.reviews, observed_at=started_at
            )
            listing_repo = ListingRepository(session)
            snapshots = SnapshotRepository(session)
            current_ids: set[int] = set()
            for listing in listings:
                record = listing_repo.get_or_create_listing(
                    listing,
                    profile_record.id,
                    observed_at=started_at,
                    tracking_run_id=run.id,
                )
                current_ids.add(record.id)
                snapshots.mark_listing_seen(run.id, record.id, observed_at=started_at)
                snapshots.save_listing_snapshot(
                    record.id, run.id, listing, observed_at=started_at
                )
            previous = self._previous_complete_run(session, profile_record.id, run.id)
            if previous is not None:
                previous_ids = set(
                    session.scalars(
                        select(ListingRecord.id)
                        .join(TrackingRunListingRecord)
                        .where(
                            TrackingRunListingRecord.tracking_run_id == previous.id
                        )
                    )
                )
                for listing_id in previous_ids - current_ids:
                    snapshots.save_listing_snapshot(
                        listing_id,
                        run.id,
                        None,
                        observed_at=started_at,
                        presence_state=PresenceState.REMOVED,
                    )
            return TrackingResult(
                run_id=run.id,
                status=TrackingRunStatus.VALID,
                profile_id=profile_record.id,
                items_fetched=len(listings),
                pages_fetched=None,
            )

    @staticmethod
    def _previous_complete_run(
        session: Session, profile_id: int, current_run_id: int
    ) -> TrackingRunRecord | None:
        return session.scalar(
            select(TrackingRunRecord)
            .where(
                TrackingRunRecord.profile_id == profile_id,
                TrackingRunRecord.id != current_run_id,
                TrackingRunRecord.status == TrackingRunStatus.VALID,
                TrackingRunRecord.items_ok.is_(True),
            )
            .order_by(TrackingRunRecord.finished_at.desc(), TrackingRunRecord.id.desc())
            .limit(1)
        )

    @staticmethod
    def _identity_profile(
        session: Session, user_id: str, profile_url: str, observed_at: datetime
    ) -> ProfileRecord:
        return cast(
            ProfileRecord,
            ProfileRepository(session).get_or_create_profile(
                Profile(user_id=user_id, url=profile_url), observed_at=observed_at
            ),
        )

    def _ensure_anchor(self, user_id: str, profile_url: str, observed_at: datetime) -> int:
        """Create only the schema-required identity for a non-valid audit run."""
        with self.session_factory.begin() as session:
            return self._identity_profile(session, user_id, profile_url, observed_at).id

    @staticmethod
    def _warn_on_discrepancy(
        stats: ProfileStats, reviews: ReviewSummary, user_id: str
    ) -> None:
        if (
            stats.rating is not None
            and reviews.rating is not None
            and stats.rating != reviews.rating
        ) or (
            stats.review_count is not None
            and reviews.review_count is not None
            and stats.review_count != reviews.review_count
        ):
            logger.warning(
                "stats_reviews_discrepancy user_id=%s stats_rating=%s reviews_rating=%s "
                "stats_review_count=%s reviews_review_count=%s",
                user_id,
                stats.rating,
                reviews.rating,
                stats.review_count,
                reviews.review_count,
            )
