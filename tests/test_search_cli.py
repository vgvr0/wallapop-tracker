import json
from decimal import Decimal

from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.models import Listing
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackedSearchRecord
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    SnapshotRepository,
    TrackedSearchRepository,
    TrackingRunRepository,
)

runner = CliRunner()


def test_search_cli_crud_and_filters(tmp_path, monkeypatch):
    path = tmp_path / "search-cli.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.close()

    added = runner.invoke(
        cli.app,
        [
            "search",
            "add",
            "--name",
            "iPhone barato",
            "--query",
            "iphone 15 pro",
            "--max-price",
            "650",
            "--include",
            "256gb",
            "--exclude",
            "roto",
        ],
    )
    assert added.exit_code == 0, added.output
    assert "id: 1" in added.output

    enabled_initial = runner.invoke(
        cli.app,
        ["search", "add", "--name", "tablet", "--query", "tablet", "--notify-on-first-run"],
    )
    assert enabled_initial.exit_code == 0, enabled_initial.output

    listed = runner.invoke(cli.app, ["search", "list"])
    assert listed.exit_code == 0 and "enabled" in listed.output
    shown = runner.invoke(cli.app, ["search", "show", "1"])
    assert shown.exit_code == 0 and '"include": ["256gb"]' in shown.output
    assert runner.invoke(cli.app, ["search", "disable", "1"]).exit_code == 0
    assert runner.invoke(cli.app, ["search", "enable", "1"]).exit_code == 0

    with Database(f"sqlite:///{path}").session() as session:
        record = session.get(TrackedSearchRecord, 1)
        assert record is not None and record.max_price == 650
        second = session.get(TrackedSearchRecord, 2)
        assert second is not None and second.notify_on_first_run is True

    removed = runner.invoke(cli.app, ["search", "delete", "1", "--yes"])
    assert removed.exit_code == 0


def test_search_explain_cli_reports_every_trace(tmp_path, monkeypatch):
    path = tmp_path / "search-explain.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    value = Listing(
        item_id="item-1",
        user_id="seller-1",
        title="iPhone 15 Pro",
        description="Con factura",
        price=Decimal("450"),
    )
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create(
            "iphone",
            name="iphone limpio",
            max_price=Decimal("500"),
            filters={
                "title_include": ["iphone"],
                "description_include": ["garantia"],
                "title_exclude": ["funda"],
                "title_first_word_include": ["iphone"],
            },
        )
        run = TrackingRunRepository(session).start_search_run(search.id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        SnapshotRepository(session).save_listing_snapshot(record.id, run.id, value)
        listing_id = record.id
    database.close()

    rejected = runner.invoke(cli.app, ["search", "explain", "1", str(listing_id)])
    assert rejected.exit_code == 0, rejected.output
    assert "NOT MATCHED" in rejected.output
    assert "✓ max_price: 450 <= 500" in rejected.output
    assert '✓ title_include: matched "iphone"' in rejected.output
    assert '✗ description_include: missing "garantia"' in rejected.output
    assert "✓ title_exclude: no excluded terms found" in rejected.output
    assert '✓ title_first_word_include: "iphone"' in rejected.output

    updated = runner.invoke(cli.app, ["search", "update", "1", "--description-include", "factura"])
    assert updated.exit_code == 0, updated.output

    matched = runner.invoke(cli.app, ["search", "explain", "1", str(listing_id)])
    assert matched.exit_code == 0, matched.output
    assert matched.output.startswith("MATCHED")
    assert "NOT MATCHED" not in matched.output
    assert '✓ description_include: matched "factura"' in matched.output

    as_json = runner.invoke(cli.app, ["search", "explain", "1", str(listing_id), "--json"])
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.stdout)
    assert payload["matched"] is True
    assert [trace["filter_name"] for trace in payload["traces"]] == [
        "max_price",
        "title_include",
        "description_include",
        "title_exclude",
        "title_first_word_include",
    ]
    assert payload["traces"][0] == {
        "filter_name": "max_price",
        "passed": True,
        "actual_value": "450.00",
        "expected_value": "500.00",
        "matched_values": [],
        "reason": None,
    }
    assert payload["warnings"] == []

    assert runner.invoke(cli.app, ["search", "explain", "999", str(listing_id)]).exit_code != 0
    assert runner.invoke(cli.app, ["search", "explain", "1", "999"]).exit_code != 0


def test_search_explain_cli_reports_unknown_filters(tmp_path, monkeypatch):
    path = tmp_path / "search-explain-unknown.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    value = Listing(
        item_id="item-1",
        user_id="seller-1",
        title="iPhone 15 Pro",
        price=Decimal("400"),
    )
    filters = {
        "models": ["iphone 15"],
        "latitude": 41.39,
        "longitude": 2.16,
        "max_distance_km": 25,
    }
    with database.transaction() as session:
        search = TrackedSearchRepository(session).create(
            "iphone", name="iphone", max_price=Decimal("500"), filters=filters
        )
        run = TrackingRunRepository(session).start_search_run(search.id)
        TrackingRunRepository(session).mark_valid(run.id, items_ok=True)
        record, _ = ListingRepository(session).get_or_create_global_listing(
            value, None, tracking_run_id=run.id
        )
        SnapshotRepository(session).save_listing_snapshot(record.id, run.id, value)
        listing_id = record.id
    database.close()

    unknown = runner.invoke(cli.app, ["search", "explain", "1", str(listing_id)])
    assert unknown.exit_code == 0, unknown.output
    assert "INCOMPLETE" in unknown.output
    assert "✓ max_price: 400 <= 500" in unknown.output
    assert "? model: model is not persisted in listing snapshots" in unknown.output
    assert "? distance: distance cannot be evaluated" in unknown.output
    assert "warning: model could not be evaluated from persisted data" in unknown.output

    as_json = runner.invoke(cli.app, ["search", "explain", "1", str(listing_id), "--json"])
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.stdout)
    assert payload["matched"] is None
    assert payload["complete"] is False
    assert [(trace["filter_name"], trace["passed"]) for trace in payload["traces"]] == [
        ("max_price", True),
        ("model", None),
        ("distance", None),
    ]

    # A known failure next to unknown conditions stays a failure, but incomplete.
    updated = runner.invoke(cli.app, ["search", "update", "1", "--title-include", "garantia"])
    assert updated.exit_code == 0, updated.output
    incomplete_failure = runner.invoke(cli.app, ["search", "explain", "1", str(listing_id)])
    assert incomplete_failure.exit_code == 0, incomplete_failure.output
    assert "NOT MATCHED (INCOMPLETE)" in incomplete_failure.output
    assert '✗ title_include: missing "garantia"' in incomplete_failure.output
