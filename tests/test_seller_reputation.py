import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.api import create_app
from wallapop_tracker.services.seller_reputation import build_seller_reputation
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ProfileRecord,
    ProfileSnapshotRecord,
    TrackedProfileRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def _snapshot(
    session, profile_id: int, when: datetime, values: dict[str, object], run_number: int
) -> None:
    run = TrackingRunRecord(
        profile_id=profile_id,
        started_at=when,
        finished_at=when + timedelta(seconds=1),
        status=TrackingRunStatus.VALID,
        profile_ok=True,
        stats_ok=True,
        reviews_ok=True,
        items_ok=True,
        idempotency_key=f"reputation-run-{profile_id}-{run_number}",
    )
    session.add(run)
    session.flush()
    session.add(
        ProfileSnapshotRecord(
            profile_id=profile_id,
            tracking_run_id=run.id,
            observed_at=when,
            **values,
        )
    )


def _profile(session, user_id: str, seller_type: str = "Private") -> ProfileRecord:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    profile = ProfileRecord(
        wallapop_user_id=user_id,
        seller_type=seller_type,
        first_seen_at=now - timedelta(days=365),
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    session.flush()
    return profile


def _current_values(reports: int, sales: int = 100) -> dict[str, object]:
    return {
        "rating": 4.84,
        "review_count": 284,
        "published_count": 5,
        "purchases_count": 20,
        "sales_count": sales,
        "sold_count": max(0, sales - 2),
        "reports_received": reports,
        "rating_1_pct": 1,
        "rating_2_pct": 1,
        "rating_3_pct": 3,
        "rating_4_pct": 20,
        "rating_5_pct": 75,
    }


def test_reputation_metrics_trends_and_peers_are_descriptive(database):
    now = datetime(2026, 9, 24, tzinfo=UTC)
    with database.transaction() as session:
        target = _profile(session, "reputation-target")
        _snapshot(
            session,
            target.id,
            now - timedelta(days=90),
            {**_current_values(5), "rating": 4.95, "review_count": 250},
            1,
        )
        _snapshot(
            session,
            target.id,
            now - timedelta(days=30),
            {**_current_values(8), "rating": 4.92, "review_count": 280, "sales_count": 90},
            2,
        )
        _snapshot(session, target.id, now, _current_values(14), 3)
        for index in range(20):
            peer = _profile(session, f"reputation-peer-{index}")
            _snapshot(session, peer.id, now, _current_values(index, 100), index + 10)

    with database.session() as session:
        insight = build_seller_reputation(session, target.id, now=now)

    assert insight.reports_received == 14
    assert insight.low_rating_count == 15
    assert insight.low_rating_ratio == pytest.approx(15 / 284)
    assert insight.reports_per_100_sales == pytest.approx(14)
    assert insight.reports_per_100_reviews == pytest.approx(14 * 100 / 284)
    assert insight.reports_delta_30d == 6
    assert insight.reports_delta_90d == 9
    assert insight.rating_delta_30d == pytest.approx(-0.08)
    assert insight.review_growth_30d == 4
    assert insight.sales_growth_30d == 10
    assert insight.peer_median_reports == pytest.approx(9.5)
    assert insight.peer_percentile == 75
    assert insight.peer_sample_size == 20
    assert insight.interpretation == "above_peer_range"


def test_reputation_nulls_zero_and_insufficient_peers(database):
    now = datetime(2026, 9, 24, tzinfo=UTC)
    with database.transaction() as session:
        empty = _profile(session, "reputation-empty")
        _snapshot(
            session,
            empty.id,
            now,
            {"review_count": None, "sales_count": 0, "reports_received": None},
            1,
        )
        target = _profile(session, "reputation-small")
        _snapshot(session, target.id, now, _current_values(0, 0), 2)
        peer = _profile(session, "reputation-one-peer")
        _snapshot(session, peer.id, now, _current_values(3, 0), 3)

    with database.session() as session:
        empty_insight = build_seller_reputation(session, empty.id, now=now)
        small_insight = build_seller_reputation(session, target.id, now=now)

    assert empty_insight.reports_per_100_sales is None
    assert empty_insight.reports_per_100_reviews is None
    assert empty_insight.low_rating_ratio is None
    assert "reports_received is unavailable" in empty_insight.warnings
    assert small_insight.reports_received == 0
    assert small_insight.reports_per_100_sales is None
    assert small_insight.peer_percentile is None
    assert small_insight.peer_sample_size == 1
    assert small_insight.data_quality == "insufficient_data"


def test_reputation_api_is_read_only_and_uses_persisted_data(database):
    now = datetime(2026, 9, 24, tzinfo=UTC)
    with database.transaction() as session:
        profile = _profile(session, "reputation-api")
        _snapshot(session, profile.id, now, _current_values(14), 1)
        profile_id = profile.id
    with TestClient(create_app(database)) as client:
        response = client.get(f"/api/v1/profiles/{profile_id}/reputation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["reports_received"] == 14
    assert payload["peer_percentile"] is None
    assert payload["warnings"]


def test_reputation_cli_json_uses_snapshot_only(tmp_path, monkeypatch):
    path = tmp_path / "reputation.db"
    url = f"sqlite:///{path}"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", url)
    database = Database(url)
    database.create_all()
    now = datetime(2026, 9, 24, tzinfo=UTC)
    with database.transaction() as session:
        profile = _profile(session, "reputation-cli")
        session.add(
            TrackedProfileRecord(
                profile_url="https://es.wallapop.com/user/reputation-cli",
                wallapop_user_id=profile.wallapop_user_id,
                alias="reputation-cli",
                profile_id=profile.id,
                added_at=now,
            )
        )
        _snapshot(session, profile.id, now, _current_values(14), 1)
    database.close()

    result = CliRunner().invoke(cli.app, ["profile", "reputation", "reputation-cli", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["reports_received"] == 14
