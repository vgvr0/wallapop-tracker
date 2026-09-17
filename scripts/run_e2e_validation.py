"""Run a small, manual end-to-end validation against public Wallapop profiles.

The script intentionally does not run unless both profile URLs are configured:

    WALLAPOP_E2E_PROFILE_URL_1=https://es.wallapop.com/user/...
    WALLAPOP_E2E_PROFILE_URL_2=https://es.wallapop.com/user/...

It keeps its history in ``data/e2e_validation.db`` and performs two immediate
captures per profile so that the resulting database and the diff service can
be checked together.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wallapop_tracker import WallapopClient
from wallapop_tracker.models import Listing, ProfileStats, ReviewSummary
from wallapop_tracker.services.diff import DiffService
from wallapop_tracker.services.tracker import ProfileTracker, TrackingResult
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    ProfileRecord,
    ProfileSnapshotRecord,
    TrackingRunListingRecord,
    TrackingRunStatus,
)

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "e2e_validation.db"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _configured_profiles() -> list[str]:
    urls = [
        os.getenv("WALLAPOP_E2E_PROFILE_URL_1"),
        os.getenv("WALLAPOP_E2E_PROFILE_URL_2"),
    ]
    return [url.strip() for url in urls if url and url.strip()]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _count(
    session: Session,
    model: Any,
    *,
    run_id: int | None = None,
    profile_id: int | None = None,
) -> int:
    query = select(func.count()).select_from(model)
    if run_id is not None:
        query = query.where(model.tracking_run_id == run_id)
    if profile_id is not None:
        query = query.where(model.profile_id == profile_id)
    return int(session.scalar(query) or 0)


def _run_counts(session: Session, run_id: int) -> dict[str, int]:
    listings_in_run = int(
        session.scalar(
            select(func.count())
            .select_from(TrackingRunListingRecord)
            .where(TrackingRunListingRecord.tracking_run_id == run_id)
        )
        or 0
    )
    return {
        "profile_snapshots": _count(session, ProfileSnapshotRecord, run_id=run_id),
        "listings": listings_in_run,
        "listing_snapshots": _count(session, ListingSnapshotRecord, run_id=run_id),
        "presence_rows": _count(session, TrackingRunListingRecord, run_id=run_id),
    }


def _total_snapshot_counts(session: Session, profile_id: int) -> dict[str, int]:
    listing_snapshots = int(
        session.scalar(
            select(func.count())
            .select_from(ListingSnapshotRecord)
            .join(ListingRecord, ListingRecord.id == ListingSnapshotRecord.listing_id)
            .where(ListingRecord.profile_id == profile_id)
        )
        or 0
    )
    return {
        "profile_snapshots": _count(session, ProfileSnapshotRecord, profile_id=profile_id),
        "listing_snapshots": listing_snapshots,
    }


def _print_capture(label: str, result: TrackingResult, counts: dict[str, int]) -> None:
    print(f"\n{label}\n{'-' * len(label)}")
    print(f"run_id: {result.run_id}")
    print(f"status: {result.status.value}")
    print(f"items_fetched: {result.items_fetched}")
    for key in ("profile_snapshots", "listings", "listing_snapshots", "presence_rows"):
        print(f"{key}: {counts[key]}")


def _db_listing_snapshots(
    session: Session, run_id: int
) -> list[tuple[ListingRecord, ListingSnapshotRecord]]:
    rows = session.execute(
        select(ListingRecord, ListingSnapshotRecord)
        .join(ListingSnapshotRecord, ListingSnapshotRecord.listing_id == ListingRecord.id)
        .where(ListingSnapshotRecord.tracking_run_id == run_id)
        .order_by(ListingRecord.wallapop_item_id)
    )
    return [(row[0], row[1]) for row in rows.all()]


async def _fetch_normalized(
    client: WallapopClient, url: str
) -> tuple[str, ProfileStats, ReviewSummary, list[Listing]]:
    user_id = await client.resolve_user_id(url)
    return (
        user_id,
        await client.get_profile_stats(user_id),
        await client.get_review_summary(user_id),
        await client.get_all_items(user_id),
    )


def _compare_first_capture(
    session: Session,
    run_id: int,
    profile: ProfileRecord,
    stats: ProfileStats,
    reviews: ReviewSummary,
    listings: Iterable[Listing],
) -> None:
    snapshot = session.scalar(
        select(ProfileSnapshotRecord).where(ProfileSnapshotRecord.tracking_run_id == run_id)
    )
    _assert(snapshot is not None, f"run {run_id}: missing ProfileSnapshot")
    assert snapshot is not None
    expected_rating = stats.rating if stats.rating is not None else reviews.rating
    expected_reviews = (
        stats.review_count if stats.review_count is not None else reviews.review_count
    )
    _assert(snapshot.review_count == expected_reviews, "ProfileSnapshot.review_count mismatch")
    _assert(
        snapshot.rating == (Decimal(str(expected_rating)) if expected_rating is not None else None),
        "ProfileSnapshot.rating mismatch",
    )
    _assert(snapshot.sold_count == stats.sold_count, "ProfileSnapshot.sold_count mismatch")

    expected = {item.item_id: item for item in listings}
    rows = _db_listing_snapshots(session, run_id)
    _assert(bool(rows), f"run {run_id}: missing ListingSnapshot rows")
    for listing_record, listing_snapshot in rows[: min(3, len(rows))]:
        item = expected.get(listing_record.wallapop_item_id)
        _assert(item is not None, f"listing {listing_record.wallapop_item_id}: missing live item")
        assert item is not None
        _assert(listing_record.profile_id == profile.id, "listing profile_id mismatch")
        _assert(
            listing_snapshot.presence_state == PresenceState.ACTIVE,
            "first capture is not active",
        )
        _assert(listing_record.wallapop_item_id == item.item_id, "wallapop_item_id mismatch")
        _assert(listing_snapshot.title == item.title, f"{item.item_id}: title mismatch")
        _assert(listing_snapshot.price == item.price, f"{item.item_id}: price mismatch")
    print(f"cross_check: OK ({min(3, len(rows))} listings + profile metrics)")


async def main() -> int:
    profiles = _configured_profiles()
    if len(profiles) != 2:
        print(
            "Live E2E skipped: configure WALLAPOP_E2E_PROFILE_URL_1 and "
            "WALLAPOP_E2E_PROFILE_URL_2 to run it."
        )
        return 0

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    database = Database(_database_url(DATABASE_PATH))
    database.create_all()
    try:
        async with WallapopClient(min_interval=0.5) as client:
            tracker = ProfileTracker(client, database)
            for index, url in enumerate(profiles, start=1):
                label = f"Profile {index}"
                first = await tracker.track_profile(url)
                _assert(first.status == TrackingRunStatus.VALID, f"{label}: first run is not valid")
                with database.session() as session:
                    profile = session.get(ProfileRecord, first.profile_id)
                    _assert(profile is not None, f"{label}: missing Profile")
                    assert profile is not None
                    counts = _run_counts(session, first.run_id)
                    totals_before_second = _total_snapshot_counts(session, profile.id)
                _assert(
                    counts["listings"] == first.items_fetched == counts["presence_rows"],
                    f"{label}: listing/presence counts are incoherent",
                )
                _print_capture(label, first, counts)

                user_id, stats, reviews, live_listings = await _fetch_normalized(client, url)
                _assert(user_id == profile.wallapop_user_id, f"{label}: profile ID mismatch")
                with database.session() as session:
                    _compare_first_capture(
                        session, first.run_id, profile, stats, reviews, live_listings
                    )

                second = await tracker.track_profile(url)
                _assert(
                    second.status == TrackingRunStatus.VALID,
                    f"{label}: second run is not valid",
                )
                with database.session() as session:
                    second_counts = _run_counts(session, second.run_id)
                    totals_after_second = _total_snapshot_counts(session, profile.id)
                    changes = DiffService(session).compare_runs(first.run_id, second.run_id)
                _assert(
                    second_counts["listings"]
                    == second.items_fetched
                    == second_counts["presence_rows"],
                    f"{label}: second listing/presence counts are incoherent",
                )
                print(
                    f"second_run: run_id={second.run_id} status={second.status.value} "
                    f"items_fetched={second.items_fetched}"
                )
                print(
                    "second_capture_counts: "
                    + ", ".join(f"{key}={value}" for key, value in second_counts.items())
                )
                if changes:
                    print("changes_detected:")
                    for change in changes:
                        print(f"- {change}")
                else:
                    print("changes: []")
                    new_profile_snapshots = (
                        totals_after_second["profile_snapshots"]
                        - totals_before_second["profile_snapshots"]
                    )
                    new_listing_snapshots = (
                        totals_after_second["listing_snapshots"]
                        - totals_before_second["listing_snapshots"]
                    )
                    print(
                        f"new_snapshots: profile={new_profile_snapshots} "
                        f"listing={new_listing_snapshots}"
                    )
                    _assert(
                        new_profile_snapshots == 0,
                        "unexpected ProfileSnapshot",
                    )
                    _assert(
                        new_listing_snapshots == 0,
                        "unexpected ListingSnapshot",
                    )
                print("idempotency: OK" if not changes else "idempotency: changes documented")
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
