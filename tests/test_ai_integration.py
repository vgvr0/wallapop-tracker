from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from sqlalchemy import create_engine, text

from alembic import command
from wallapop_tracker.ai.models import (
    ListingAIAnalysis,
    ListingAnalysisContext,
    ListingAnalysisMetadata,
    ListingAnalysisResult,
)
from wallapop_tracker.ai.service import ListingAIAnalysisService
from wallapop_tracker.ai.storage import ListingAIRepository, ListingNotFoundError
from wallapop_tracker.api.app import create_app
from wallapop_tracker.observability import Metrics
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    ProfileRecord,
    TrackingRunRecord,
)


class FakeAnalyzer:
    provider_name = "fake"
    model = "offline"

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, context: ListingAnalysisContext) -> ListingAnalysisResult:
        self.calls += 1
        return ListingAnalysisResult(
            analysis=ListingAIAnalysis(
                condition_assessment="good",
                condition_confidence=0.8,
                semantic_score=82,
                risk_score=24,
                deal_quality="good",
                deal_confidence=0.8,
                summary=f"Analysis for {context.listing_id}",
            ),
            metadata=ListingAnalysisMetadata(
                provider=self.provider_name,
                model=self.model,
                prompt_version="listing-analysis-v1",
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                latency_ms=1.0,
            ),
        )


def add_listing(database: Database) -> int:
    now = datetime.now(UTC)
    with database.transaction() as session:
        profile = ProfileRecord(
            wallapop_user_id="seller-1",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
        run = TrackingRunRecord(profile_id=profile.id, started_at=now)
        session.add(run)
        session.flush()
        listing = ListingRecord(
            marketplace="wallapop",
            external_id="item-1",
            wallapop_item_id="item-1",
            profile_id=profile.id,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(listing)
        session.flush()
        session.add(
            ListingSnapshotRecord(
                listing_id=listing.id,
                tracking_run_id=run.id,
                observed_at=now,
                title="Phone",
                description="Works perfectly. Small scratch.",
                price=Decimal("650"),
                currency="EUR",
                condition_label="good",
                brand="Example",
            )
        )
        return listing.id


@pytest.mark.asyncio
async def test_service_cache_force_and_context(database: Database) -> None:
    listing_id = add_listing(database)
    analyzer = FakeAnalyzer()
    metrics = Metrics(CollectorRegistry())
    service = ListingAIAnalysisService(database, analyzer, metrics)

    first = await service.assess(listing_id)
    cached = await service.assess(listing_id)
    forced = await service.assess(listing_id, force=True)

    assert first.cache_hit is False
    assert cached.cache_hit is True
    assert forced.cache_hit is False
    assert analyzer.calls == 2
    with database.session() as session:
        records = ListingAIRepository(session).list_assessments(listing_id)
        assert len(records) == 1
        assert ListingAIRepository(session).build_context(listing_id).price == Decimal("650.00")


@pytest.mark.asyncio
async def test_service_missing_listing_does_not_call_provider(database: Database) -> None:
    analyzer = FakeAnalyzer()
    service = ListingAIAnalysisService(database, analyzer, Metrics(CollectorRegistry()))
    with pytest.raises(ListingNotFoundError):
        await service.assess(123)
    assert analyzer.calls == 0


def test_api_post_get_and_secret_never_leaks(database: Database) -> None:
    listing_id = add_listing(database)
    analyzer = FakeAnalyzer()
    service = ListingAIAnalysisService(database, analyzer, Metrics(CollectorRegistry()))
    client = TestClient(create_app(database, ai_service=service))

    response = client.post(f"/api/v1/listings/{listing_id}/ai-assessment")
    assert response.status_code == 200
    assert response.json()["cache_hit"] is False
    cached = client.post(f"/api/v1/listings/{listing_id}/ai-assessment")
    assert cached.json()["cache_hit"] is True
    latest = client.get(f"/api/v1/listings/{listing_id}/ai-assessment")
    assert latest.status_code == 200
    assert "super-secret-test-key" not in latest.text
    assert client.get("/api/v1/listings/999999/ai-assessment").status_code == 404


def test_api_disabled_by_default(database: Database, monkeypatch: Any) -> None:
    monkeypatch.delenv("WALLAPOP_AI_ENABLED", raising=False)
    client = TestClient(create_app(database))
    assert client.post("/api/v1/listings/1/ai-assessment").status_code == 409


def test_ai_migration_creates_identity_and_payload_columns(tmp_path: Path) -> None:
    path = tmp_path / "ai-migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(listing_ai_assessments)"))
        }
        indexes = {
            row[1] for row in connection.execute(text("PRAGMA index_list(listing_ai_assessments)"))
        }
    assert {"listing_id", "input_hash", "analysis_json", "semantic_score", "created_at"} <= columns
    assert "ix_listing_ai_assessments_listing_created" in indexes
