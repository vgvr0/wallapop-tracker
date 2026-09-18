from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackedSearchRecord

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
