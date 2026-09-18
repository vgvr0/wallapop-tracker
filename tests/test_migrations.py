from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def _config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_separate_tracking_run_sources_migrates_synthetic_profiles(tmp_path):
    database_path = tmp_path / "legacy.db"
    config = _config(database_path)
    command.upgrade(config, "0007_search_tracking")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        # The live metadata used by the historical 0001 migration already
        # knows the new CHECK. Ignore it only while constructing legacy rows.
        connection.execute(text("PRAGMA ignore_check_constraints = ON"))
        connection.execute(
            text(
                "INSERT INTO profiles "
                "(wallapop_user_id, first_seen_at, last_seen_at, created_at, updated_at) "
                "VALUES ('tracked-search:7', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        profile_id = connection.execute(
            text("SELECT id FROM profiles WHERE wallapop_user_id = 'tracked-search:7'")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO tracked_searches "
                "(query, enabled, interval_seconds, created_at, updated_at) "
                "VALUES ('phone', 1, 600, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tracking_runs "
                "(profile_id, tracked_search_id, started_at, finished_at, status, "
                "profile_ok, stats_ok, reviews_ok, items_ok) "
                "VALUES (:profile_id, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'valid', 0, 0, 0, 1)"
            ),
            {"profile_id": profile_id},
        )
        connection.execute(
            text(
                "INSERT INTO listings "
                "(wallapop_item_id, profile_id, first_seen_at, last_seen_at, "
                "created_at, updated_at) "
                "VALUES ('item-1', :profile_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"profile_id": profile_id},
        )
        listing_id = connection.execute(
            text("SELECT id FROM listings WHERE wallapop_item_id = 'item-1'")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO listing_snapshots "
                "(listing_id, tracking_run_id, observed_at, presence_state, price) "
                "VALUES (:listing_id, 1, CURRENT_TIMESTAMP, 'active', 100)"
            ),
            {"listing_id": listing_id},
        )
        connection.execute(
            text(
                "INSERT INTO tracking_events "
                "(event_type, idempotency_key, listing_id, tracking_run_id, "
                "tracked_search_id, created_at) VALUES "
                "('NEW_LISTING', 'legacy-event-1', :listing_id, 1, 1, CURRENT_TIMESTAMP)"
            ),
            {"listing_id": listing_id},
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        run = connection.execute(
            text("SELECT profile_id, tracked_search_id FROM tracking_runs")
        ).one()
        listing_profile = connection.execute(
            text("SELECT profile_id FROM listings WHERE wallapop_item_id = 'item-1'")
        ).scalar_one()
        synthetic_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM profiles "
                "WHERE wallapop_user_id = 'tracked-search:7'"
            )
        ).scalar_one()
        snapshot_count = connection.execute(
            text("SELECT COUNT(*) FROM listing_snapshots")
        ).scalar_one()
        event_count = connection.execute(
            text("SELECT COUNT(*) FROM tracking_events")
        ).scalar_one()

    assert run.profile_id is None
    assert run.tracked_search_id == 1
    assert listing_profile is None
    assert synthetic_count == 0
    assert snapshot_count == 1
    assert event_count == 1
