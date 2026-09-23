from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def _config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def test_deal_score_snapshots_migration_from_0020(tmp_path):
    database_path = tmp_path / "deal-score.db"
    config = _config(database_path)
    command.upgrade(config, "0020_advanced_alerts")
    command.upgrade(config, "0021_deal_score_snapshots")
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(deal_score_snapshots)"))}
        foreign_keys = {row[2] for row in connection.execute(text("PRAGMA foreign_key_list(deal_score_snapshots)"))}
        indexes = {row[1] for row in connection.execute(text("PRAGMA index_list(deal_score_snapshots)"))}
    assert columns == {"id", "listing_id", "tracked_search_id", "score", "computed_at"}
    assert foreign_keys == {"listings", "tracked_searches"}
    assert "ix_deal_score_snapshots_context_time" in indexes


def test_health_migrations_upgrade_from_0015_preserves_existing_rows(tmp_path):
    database_path = tmp_path / "health-upgrade.db"
    config = _config(database_path)
    command.upgrade(config, "0015_listing_condition_codes")
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO profiles (wallapop_user_id, first_seen_at, last_seen_at, created_at, updated_at) "
            "VALUES ('migration-user', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        profile_id = connection.execute(text(
            "SELECT id FROM profiles WHERE wallapop_user_id = 'migration-user'"
        )).scalar_one()
        connection.execute(text(
            "INSERT INTO tracking_runs (profile_id, started_at, finished_at, status, "
            "profile_ok, stats_ok, reviews_ok, items_ok) "
            "VALUES (:profile_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'valid', 1, 1, 1, 1)"
        ), {"profile_id": profile_id})
    command.upgrade(config, "0017_schema_drift_events")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM tracking_runs")).scalar_one() == 1
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(tracking_runs)"))}
        assert "health_status" in columns
        assert connection.execute(text("SELECT COUNT(*) FROM schema_drift_events")).scalar_one() == 0


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


def test_notification_delivery_migration_from_0008(tmp_path):
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, "0008_separate_tracking_run_sources")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(notification_deliveries)"))
        }
        indexes = {
            row[1]
            for row in connection.execute(text("PRAGMA index_list(notification_deliveries)"))
        }

    assert columns == {
        "id",
        "event_id",
        "channel",
        "destination",
        "status",
        "attempts",
        "last_error",
        "created_at",
        "updated_at",
        "delivered_at",
        "next_attempt_at",
        "processing_started_at",
        "claim_expires_at",
        "claimed_by",
    }
    assert "ix_notification_deliveries_status_created" in indexes


def test_marketplace_identity_migration_preserves_listing_history(tmp_path):
    database_path = tmp_path / "marketplace.db"
    config = _config(database_path)
    command.upgrade(config, "0012_possible_relistings")
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO listings "
                "(wallapop_item_id, first_seen_at, last_seen_at, created_at, updated_at) "
                "VALUES ('ABC', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tracked_searches "
                "(query, enabled, interval_seconds, created_at, updated_at) "
                "VALUES ('phone', 1, 600, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    command.upgrade(config, "0013_marketplace_identity")
    with engine.connect() as connection:
        listing = connection.execute(
            text("SELECT marketplace, external_id FROM listings")
        ).one()
        search_marketplace = connection.execute(
            text("SELECT marketplace FROM tracked_searches")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO listings "
                "(marketplace, external_id, first_seen_at, last_seen_at, created_at, updated_at) "
                "VALUES ('vinted', 'ABC', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    assert listing == ("wallapop", "ABC")
    assert search_marketplace == "wallapop"


def test_tracked_listing_migration_from_0009(tmp_path):
    database_path = tmp_path / "tracked-listings.db"
    config = _config(database_path)
    command.upgrade(config, "0009_notification_deliveries")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        tracked_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(tracked_listings)"))
        }
        run_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(tracking_runs)"))
        }
        run_foreign_keys = {
            row[2]
            for row in connection.execute(text("PRAGMA foreign_key_list(tracking_runs)"))
        }

    assert {
        "id",
        "listing_id",
        "alias",
        "enabled",
        "interval_seconds",
        "last_run_at",
        "last_run_status",
        "last_tracking_run_id",
        "notes",
        "created_at",
            "updated_at",
            "target_price",
            "percentage_drop_threshold",
            "deal_score_threshold",
            "notify_on_30d_low",
            "notify_on_90d_low",
            "notify_on_all_time_low",
            "claimed_at",
            "claim_expires_at",
            "claimed_by",
        } == tracked_columns
    assert "tracked_listing_id" in run_columns
    assert "tracked_listings" in run_foreign_keys


def test_search_initial_baseline_migration_from_0010(tmp_path):
    database_path = tmp_path / "search-baseline.db"
    config = _config(database_path)
    command.upgrade(config, "0010_tracked_listings")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tracked_searches "
                "(query, enabled, interval_seconds, created_at, updated_at) "
                "VALUES ('no-run', 1, 600, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('valid-run', 1, 600, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('failed-run', 1, 600, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tracking_runs "
                "(tracked_search_id, started_at, finished_at, status, "
                "profile_ok, stats_ok, reviews_ok, items_ok) VALUES "
                "(2, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'valid', 0, 0, 0, 1), "
                "(3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'failed', 0, 0, 0, 0)"
            )
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        searches = connection.execute(
            text(
                "SELECT id, notify_on_first_run FROM tracked_searches ORDER BY id"
            )
        ).all()
        run_count = connection.execute(text("SELECT COUNT(*) FROM tracking_runs")).scalar_one()
        column = connection.execute(
            text(
                'SELECT "notnull", dflt_value FROM pragma_table_info(\'tracked_searches\') '
                "WHERE name = 'notify_on_first_run'"
            )
        ).one()

    assert [(row[0], row[1]) for row in searches] == [(1, 1), (2, 1), (3, 1)]
    assert run_count == 2
    assert column[0] == 1
    assert column[1] == "'0'"


def test_possible_relisting_migration_from_0011(tmp_path):
    database_path = tmp_path / "relisting.db"
    config = _config(database_path)
    command.upgrade(config, "0011_search_initial_baseline")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        listing_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(listings)"))
        }
        relisting_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(possible_relistings)"))
        }
        indexes = {
            row[1]
            for row in connection.execute(text("PRAGMA index_list(possible_relistings)"))
        }

    assert "seller_user_id" in listing_columns
    assert relisting_columns == {
        "id",
        "previous_listing_id",
        "current_listing_id",
        "score",
        "reasons_json",
        "detected_at",
        "status",
        "event_id",
    }
    assert "ix_possible_relistings_score_detected" in indexes
