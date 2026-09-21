from datetime import UTC, datetime, timedelta

from wallapop_tracker.health import classify_run, health_summary, suspicious_zero
from wallapop_tracker.schema_monitor import (
    observe_schema,
    schema_diff,
    schema_fingerprint,
)
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    SchemaDriftEventRecord,
    TrackedSearchRecord,
    TrackingRunRecord,
    TrackingRunStatus,
)


def test_health_classification_rules():
    assert classify_run(completed=True) == "SUCCESS"
    assert classify_run(completed=True, parse_errors=1) == "DEGRADED"
    assert classify_run(completed=True, http_429=1) == "DEGRADED"
    assert classify_run(completed=False) == "FAILED"


def test_schema_ignores_values_and_key_order():
    first = {"items": [{"title": "one", "price": {"amount": 1}}], "meta": {"next": "a"}}
    second = {"meta": {"next": "b"}, "items": [{"title": "two", "price": {"amount": 999}}]}
    assert schema_fingerprint(first)[0] == schema_fingerprint(second)[0]


def test_schema_reports_removed_and_added_paths():
    old = {"item": {"price": {"amount": 1}}}
    new = {"item": {"price": {"value": 1}}}
    diff = schema_diff(set(schema_fingerprint(old)[1]), set(schema_fingerprint(new)[1]))
    assert any("amount" in path for path in diff["missing_paths"])
    assert any("value" in path for path in diff["new_paths"])


def test_empty_history_is_not_suspicious_and_three_positive_runs_are():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    now = datetime.now(UTC)
    with database.transaction() as session:
        search = TrackedSearchRecord(query="x", created_at=now, updated_at=now)
        session.add(search)
        session.flush()
        assert not suspicious_zero(session, TrackingRunRecord.tracked_search_id, search.id, current=0)
        for index in range(3):
            session.add(TrackingRunRecord(
                tracked_search_id=search.id, started_at=now - timedelta(minutes=index + 1),
                finished_at=now - timedelta(minutes=index + 1), status=TrackingRunStatus.VALID,
                items_fetched=4, items_scanned=4, health_status="SUCCESS",
            ))
        session.flush()
        assert suspicious_zero(session, TrackingRunRecord.tracked_search_id, search.id, current=0)
    database.close()


def test_consecutive_failures_recovery_and_aggregates():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    now = datetime.now(UTC)
    with database.transaction() as session:
        search = TrackedSearchRecord(query="x", created_at=now, updated_at=now)
        session.add(search)
        session.flush()
        statuses = [("FAILED", 400), ("FAILED", 300), ("FAILED", 200), ("SUCCESS", 100)]
        for index, (status, duration) in enumerate(statuses):
            timestamp = now - timedelta(minutes=index + 1)
            session.add(TrackingRunRecord(
                tracked_search_id=search.id, started_at=timestamp, finished_at=timestamp,
                status=TrackingRunStatus.FAILED if status == "FAILED" else TrackingRunStatus.VALID,
                health_status=status, duration_ms=duration, items_scanned=1,
            ))
        session.flush()
        summary = health_summary(session, search_id=search.id)
        assert summary["status"] == "FAILING"
        assert summary["consecutive_failures"] == 3
        assert summary["runs_24h"] == 4
        assert summary["failed_runs_24h"] == 3
        assert summary["success_rate_24h"] == 0.25
        assert summary["average_duration_ms_24h"] == 250
        assert summary["p95_duration_ms_24h"] == 385
        assert summary["last_success_at"] is not None
        assert summary["last_failure_at"] is not None
        session.add(TrackingRunRecord(
            tracked_search_id=search.id, started_at=now, finished_at=now,
            status=TrackingRunStatus.VALID, health_status="SUCCESS", duration_ms=100,
        ))
        session.flush()
        recovered = health_summary(session, search_id=search.id)
        assert recovered["consecutive_failures"] == 0
    database.close()


def test_schema_drift_is_idempotent_and_detects_second_transition():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    with database.transaction() as session:
        schema_a = {"item": {"price": {"amount": 1}}}
        schema_b = {"item": {"price": {"value": 1}}}
        schema_c = {"item": {"price": {"value": "1"}}}
        assert observe_schema(session, "listing_detail", schema_a) is None
        first = observe_schema(session, "listing_detail", schema_b)
        assert first is not None
        assert observe_schema(session, "listing_detail", schema_b) is None
        assert observe_schema(session, "listing_detail", schema_b) is None
        second = observe_schema(session, "listing_detail", schema_c)
        assert second is not None
        assert session.query(SchemaDriftEventRecord).count() == 2
    database.close()
