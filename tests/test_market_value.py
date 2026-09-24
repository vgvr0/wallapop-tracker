from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from wallapop_tracker.services.market_value import MarketConfidence, MarketValueService
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    ProfileRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


def add_listing(
    session,
    number,
    *,
    price,
    title="iPhone 15 Pro 256GB",
    brand="Apple",
    category="phones",
    condition="good",
    observed=None,
    currency="EUR",
):
    now = observed or datetime.now(UTC)
    profile = ProfileRecord(
        wallapop_user_id=f"mv-profile-{number}",
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    session.flush()
    run = TrackingRunRecord(
        profile_id=profile.id, started_at=now, finished_at=now, status=TrackingRunStatus.VALID
    )
    session.add(run)
    session.flush()
    listing = ListingRecord(
        external_id=f"mv-{number}",
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
            title=title,
            price=price,
            currency=currency,
            brand=brand,
            category_id=category,
            condition=condition,
        )
    )
    return listing


def estimate(database, prices, **kwargs):
    now = datetime.now(UTC)
    with database.transaction() as session:
        target = add_listing(session, 1, price=Decimal("80"), observed=now)
        for index, price in enumerate(prices, 2):
            add_listing(session, index, price=Decimal(str(price)), observed=now, **kwargs)
        return MarketValueService(session).estimate(target.id)


def test_statistics_discount_percentile_and_outliers(database):
    result = estimate(database, [100, 105, 110, 115, 1000])
    assert result.median_price == Decimal("107.50")
    assert result.p25_price == Decimal("103.75")
    assert result.p75_price == Decimal("111.25")
    assert result.iqr == Decimal("7.50")
    assert result.selection_summary.outlier_count == 1
    assert result.selection_summary.accepted_count == 4
    assert result.discount_vs_median == Decimal("-0.2558139534883720930232558140")
    assert result.percentile == 0.0


def test_percentile_and_discount_positive(database):
    result = estimate(database, [100, 110, 120])
    assert result.percentile == 0.0
    assert result.discount_vs_median == Decimal("-0.2727272727272727272727272727")


def test_target_excluded_and_one_listing_one_comparable(database):
    now = datetime.now(UTC)
    with database.transaction() as session:
        target = add_listing(session, 1, price=Decimal("100"), observed=now)
        candidate = add_listing(session, 2, price=Decimal("120"), observed=now)
        profile_id = session.scalars(select(ProfileRecord.id)).all()[-1]
        duplicate_run = TrackingRunRecord(
            profile_id=profile_id, started_at=now, finished_at=now, status=TrackingRunStatus.VALID
        )
        session.add(duplicate_run)
        session.flush()
        run_id = duplicate_run.id
        session.add(
            ListingSnapshotRecord(
                listing_id=candidate.id,
                tracking_run_id=run_id,
                observed_at=now + timedelta(seconds=1),
                title="iPhone 15 Pro 256GB",
                price=Decimal("125"),
                currency="EUR",
                brand="Apple",
                category_id="phones",
                condition="good",
            )
        )
        result = MarketValueService(session).estimate(target.id)
    assert result.sample_size == 1
    assert [item.listing_id for item in result.comparables] == [candidate.id]


def test_similarity_signals_and_wrong_brand(database):
    now = datetime.now(UTC)
    with database.transaction() as session:
        target = add_listing(session, 1, price=Decimal("100"), observed=now)
        strong = add_listing(session, 2, price=Decimal("110"), observed=now)
        weak = add_listing(
            session,
            3,
            price=Decimal("90"),
            title="Generic phone",
            brand="Samsung",
            condition=None,
            observed=now,
        )
        result = MarketValueService(session).estimate(target.id)
    assert result.comparables[0].listing_id == strong.id
    assert "same brand" in result.comparables[0].matched_signals
    assert all(item.listing_id != weak.id for item in result.comparables)


@pytest.mark.parametrize("prices", [[], [100], [100, 110]])
def test_small_samples_are_insufficient(database, prices):
    result = estimate(database, prices)
    assert result.confidence_level == MarketConfidence.INSUFFICIENT
    assert result.confidence <= 1


def test_time_window_and_missing_price(database):
    now = datetime.now(UTC)
    with database.transaction() as session:
        target = add_listing(session, 1, price=Decimal("100"), observed=now)
        add_listing(session, 2, price=None, observed=now)
        add_listing(session, 3, price=Decimal("110"), observed=now - timedelta(days=60))
        result = MarketValueService(session).estimate(target.id)
    assert result.sample_size == 0
    assert result.median_price is None


def test_zero_price_is_safe(database):
    result = estimate(database, [0, 100])
    assert result.sample_size == 1
    assert result.discount_vs_median is not None
    assert "NaN" not in str(result.to_dict())
    assert "Infinity" not in str(result.to_dict())
