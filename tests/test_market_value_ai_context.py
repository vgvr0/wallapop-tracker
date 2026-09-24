from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from wallapop_tracker.ai.storage import ListingAIRepository
from wallapop_tracker.services.market_value import MarketConfidence
from wallapop_tracker.storage.models import ListingRecord


def _listing(session):
    now = datetime.now(UTC)
    row = ListingRecord(
        external_id="ai-market", first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now
    )
    session.add(row)
    session.flush()
    return row


def test_reliable_market_context_fields_are_propagated(database, monkeypatch):
    estimate = SimpleNamespace(
        confidence_level=MarketConfidence.HIGH,
        median_price=Decimal("700"),
        p25_price=Decimal("650"),
        p75_price=Decimal("780"),
        sample_size=24,
    )
    monkeypatch.setattr(
        "wallapop_tracker.ai.storage.MarketValueService.estimate", lambda self, listing_id: estimate
    )
    with database.transaction() as session:
        context = ListingAIRepository(session).build_context(_listing(session).id)
    assert (
        context.market_median_price,
        context.market_p25,
        context.market_p75,
        context.market_sample_size,
    ) == (Decimal("700"), Decimal("650"), Decimal("780"), 24)


def test_insufficient_market_context_is_not_propagated(database, monkeypatch):
    estimate = SimpleNamespace(
        confidence_level=MarketConfidence.INSUFFICIENT,
        median_price=Decimal("700"),
        p25_price=Decimal("650"),
        p75_price=Decimal("780"),
        sample_size=2,
    )
    monkeypatch.setattr(
        "wallapop_tracker.ai.storage.MarketValueService.estimate", lambda self, listing_id: estimate
    )
    with database.transaction() as session:
        context = ListingAIRepository(session).build_context(_listing(session).id)
    assert context.market_median_price is None
    assert context.market_p25 is None
    assert context.market_p75 is None
    assert context.market_sample_size is None
