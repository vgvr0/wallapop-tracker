from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.domain.deal_scoring import DealScoreStatus, DealScoringPolicy
from wallapop_tracker.services.deal_scoring import DealScoringService
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PresenceState,
    TrackingRunListingRecord,
)
from wallapop_tracker.storage.repositories import (
    SearchMatchRepository,
    TrackedSearchRepository,
    TrackingRunRepository,
)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def _dataset(database: Database, target_price: str = "550") -> tuple[int, int]:
    observed = datetime.now(UTC) - timedelta(minutes=10)
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create("phones")
        run = TrackingRunRepository(session).start_search_run(search.id, started_at=observed)
        TrackingRunRepository(session).mark_valid(run.id, items_fetched=6, items_ok=True)
        prices = [target_price, "600", "700", "700", "800", "900"]
        listings = [
            ListingRecord(
                wallapop_item_id=f"item-{index}",
                seller_user_id="seller",
                first_seen_at=observed,
                last_seen_at=observed,
                created_at=observed,
                updated_at=observed,
            )
            for index in range(len(prices))
        ]
        session.add_all(listings)
        session.flush()
        matches = SearchMatchRepository(session)
        for listing, price in zip(listings, prices, strict=True):
            matches.touch(search.id, listing.id, observed)
            session.add(
                TrackingRunListingRecord(
                    tracking_run_id=run.id, listing_id=listing.id, observed_at=observed
                )
            )
            session.add(
                ListingSnapshotRecord(
                    listing_id=listing.id,
                    tracking_run_id=run.id,
                    observed_at=observed,
                    title=listing.wallapop_item_id,
                    price=Decimal(price),
                    presence_state=PresenceState.ACTIVE,
                )
            )
        return search.id, listings[0].id


def test_score_is_explainable_and_deterministic(database):
    search_id, listing_id = _dataset(database)
    with database.session() as session:
        service = DealScoringService(session)
        first = service.score_listing(listing_id, search_id)
        second = service.score_listing(listing_id, search_id)
        assert first.status == DealScoreStatus.SCORED
        assert first.score is not None and first.score > 50
        assert first.score == second.score
        assert {reason.name for reason in first.reasons} >= {
            "price_below_median",
            "price_below_p25",
            "fresh_listing",
            "market_depth",
        }
        assert first.confidence > 0


def test_expensive_listing_scores_below_neutral(database):
    search_id, listing_id = _dataset(database, "950")
    with database.session() as session:
        result = DealScoringService(session).score_listing(listing_id, search_id)
        assert result.score is not None and result.score < 50


def test_insufficient_comparables_and_missing_price(database):
    search_id, listing_id = _dataset(database)
    with database.transaction() as session:
        for row in session.scalars(
            select(ListingSnapshotRecord).where(ListingSnapshotRecord.listing_id != listing_id)
        ).all()[2:]:
            session.delete(row)
    with database.session() as session:
        result = DealScoringService(
            session, DealScoringPolicy(minimum_comparables=10)
        ).score_listing(listing_id, search_id)
        assert result.score is None
        assert result.status == DealScoreStatus.INSUFFICIENT_DATA


def test_score_cli_single_and_batch(tmp_path, monkeypatch):
    path = tmp_path / "score.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    search_id, listing_id = _dataset(database)
    database.close()
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    runner = CliRunner()
    single = runner.invoke(
        cli.app,
        ["score", "listing", str(listing_id), "--search-id", str(search_id)],
    )
    batch = runner.invoke(cli.app, ["score", "search", str(search_id), "--limit", "2"])
    assert single.exit_code == 0, single.output
    assert "Deal score:" in single.output
    assert batch.exit_code == 0, batch.output
    assert "Score\tConfidence" in batch.output
