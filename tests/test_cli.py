from decimal import Decimal

from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.models import Listing, Profile
from wallapop_tracker.services.tracker import TrackingResult
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackingRunStatus
from wallapop_tracker.storage.repositories import (
    ListingRepository,
    TrackedListingRepository,
    TrackedProfileRepository,
    TrackedSearchRepository,
)

runner = CliRunner()


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def resolve_user_id(self, url):
        return "user-a"

    async def get_profile(self, user_id):
        return Profile(user_id=user_id, name="Test")


def _database(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.close()
    return path


def test_cli_add_list_enable_disable_remove(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "WallapopClient", FakeClient)
    added = runner.invoke(cli.app, ["add", "https://es.wallapop.com/user/a", "--alias", " Andrey "])
    assert added.exit_code == 0
    assert "alias: andrey" in added.output
    listed = runner.invoke(cli.app, ["list"])
    assert listed.exit_code == 0 and "enabled" in listed.output
    disabled = runner.invoke(cli.app, ["disable", "andrey"])
    assert disabled.exit_code == 0 and "disabled" in disabled.output
    enabled = runner.invoke(cli.app, ["enable", "andrey"])
    assert enabled.exit_code == 0 and "enabled" in enabled.output
    removed = runner.invoke(cli.app, ["remove", "andrey", "--yes"])
    assert removed.exit_code == 0
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").session() as session:
        assert TrackedProfileRepository(session).list_all() == []


def test_search_alerts_set_persists_configuration(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").transaction() as session:
        search_id = TrackedSearchRepository(session).create("camera").id
    result = runner.invoke(
        cli.app,
        [
            "search",
            "alerts",
            "set",
            str(search_id),
            "--percentage-drop",
            "10",
            "--deal-score-threshold",
            "80",
            "--notify-30d-low",
        ],
    )
    assert result.exit_code == 0
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").session() as session:
        row = TrackedSearchRepository(session).get(search_id)
        assert row is not None
        assert row.percentage_drop_threshold == 10
        assert row.deal_score_threshold == 80
        assert row.notify_on_30d_low is True


def test_advanced_alert_cli_listing_and_search_round_trip_and_clear(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").transaction() as session:
        listing, _ = ListingRepository(session).get_or_create_global_listing(
            Listing(
                item_id="item-1",
                user_id="seller",
                title="Camera",
                price=Decimal("100"),
                currency="EUR",
                url="https://example/item-1",
            ),
            None,
        )
        tracked_id = TrackedListingRepository(session).create(listing.id, "camera").id
        search_id = TrackedSearchRepository(session).create("camera").id
    listing_set = runner.invoke(
        cli.app,
        [
            "listing",
            "alerts",
            "set",
            "camera",
            "--target-price",
            "50",
            "--percentage-drop",
            "10",
            "--deal-score-threshold",
            "80",
            "--notify-30d-low",
            "--notify-90d-low",
            "--notify-all-time-low",
        ],
    )
    assert listing_set.exit_code == 0
    search_set = runner.invoke(
        cli.app,
        [
            "search",
            "alerts",
            "set",
            str(search_id),
            "--percentage-drop",
            "10",
            "--deal-score-threshold",
            "80",
            "--notify-30d-low",
            "--notify-90d-low",
            "--notify-all-time-low",
        ],
    )
    assert search_set.exit_code == 0
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").session() as session:
        listing_row = TrackedListingRepository(session).get(tracked_id)
        search_row = TrackedSearchRepository(session).get(search_id)
        assert listing_row is not None and listing_row.target_price == 50
        assert listing_row.notify_on_90d_low and listing_row.notify_on_all_time_low
        assert search_row is not None and search_row.deal_score_threshold == 80
        assert search_row.notify_on_90d_low and search_row.notify_on_all_time_low
    cleared = runner.invoke(
        cli.app,
        [
            "listing",
            "alerts",
            "set",
            "camera",
            "--clear-target-price",
            "--clear-percentage-drop",
            "--clear-deal-score-threshold",
        ],
    )
    assert cleared.exit_code == 0
    search_cleared = runner.invoke(
        cli.app,
        [
            "search",
            "alerts",
            "set",
            str(search_id),
            "--clear-percentage-drop",
            "--clear-deal-score-threshold",
        ],
    )
    assert search_cleared.exit_code == 0
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").session() as session:
        assert TrackedListingRepository(session).get(tracked_id).target_price is None
        assert TrackedSearchRepository(session).get(search_id).deal_score_threshold is None


def test_cli_add_duplicate_and_missing_alias(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "WallapopClient", FakeClient)
    command = ["add", "https://es.wallapop.com/user/a", "--alias", "andrey"]
    assert runner.invoke(cli.app, command).exit_code == 0
    duplicate = runner.invoke(cli.app, command)
    assert duplicate.exit_code != 0 and "already exists" in duplicate.output
    missing = runner.invoke(cli.app, ["enable", "nobody"])
    assert missing.exit_code != 0 and "Unknown alias" in str(missing.exception)


def test_cli_run_and_run_all_continue(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    with Database(f"sqlite:///{tmp_path / 'cli.db'}").transaction() as session:
        repo = TrackedProfileRepository(session)
        repo.create("https://es.wallapop.com/user/a", "user-a", "andrey")
        repo.create("https://es.wallapop.com/user/b", "user-b", "disabled")
        repo.disable("disabled")

    class FakeTracker:
        calls = []

        def __init__(self, client, database):
            pass

        async def track_profile(self, url):
            self.calls.append(url)
            return TrackingResult(7, TrackingRunStatus.VALID, None, 3, 1)

    monkeypatch.setattr(cli, "WallapopClient", FakeClient)
    monkeypatch.setattr(cli, "ProfileTracker", FakeTracker)
    result = runner.invoke(cli.app, ["run", "andrey"])
    assert result.exit_code == 0 and "valid" in result.output
    all_result = runner.invoke(cli.app, ["run-all"])
    assert all_result.exit_code == 0 and "valid: 1" in all_result.output


def test_cli_notifications_list_and_retry(tmp_path, monkeypatch):
    _database(tmp_path, monkeypatch)
    listed = runner.invoke(cli.app, ["notifications", "list"])
    retried = runner.invoke(cli.app, ["notifications", "retry"])
    assert listed.exit_code == 0
    assert retried.exit_code == 0 and "delivered: 0" in retried.output
