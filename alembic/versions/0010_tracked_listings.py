"""Add tracked listing source and monitoring configuration."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0010_tracked_listings"
down_revision = "0009_notification_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_status", sa.String(20)),
        sa.Column("last_tracking_run_id", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("interval_seconds > 0", name="ck_tracked_listing_interval"),
        sa.UniqueConstraint("alias", name="uq_tracked_listings_alias"),
        sa.UniqueConstraint("listing_id", name="uq_tracked_listings_listing"),
    )
    op.create_index(
        "ix_tracked_listings_enabled_last_run",
        "tracked_listings",
        ["enabled", "last_run_at"],
    )
    if op.get_bind().dialect.name == "sqlite":
        _rebuild_sqlite_tracking_runs()
    else:
        existing_columns = {
            column["name"] for column in inspect(op.get_bind()).get_columns("tracking_runs")
        }
        if "tracked_listing_id" not in existing_columns:
            op.add_column(
                "tracking_runs", sa.Column("tracked_listing_id", sa.Integer(), nullable=True)
            )
        op.create_foreign_key(
            "fk_tracking_runs_tracked_listing",
            "tracking_runs",
            "tracked_listings",
            ["tracked_listing_id"],
            ["id"],
        )
        op.drop_constraint("ck_tracking_runs_exactly_one_source", "tracking_runs", type_="check")
        op.create_check_constraint(
            "ck_tracking_runs_exactly_one_source",
            "tracking_runs",
            "((CASE WHEN profile_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN tracked_search_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN tracked_listing_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM tracked_listings")).scalar_one():
        raise RuntimeError("Cannot downgrade 0010 while tracked listings exist")
    if connection.execute(
        sa.text("SELECT COUNT(*) FROM tracking_runs WHERE tracked_listing_id IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("Cannot downgrade 0010 while listing runs exist")
    if connection.dialect.name == "sqlite":
        _rebuild_sqlite_tracking_runs(downgrade=True)
    else:
        with op.batch_alter_table("tracking_runs", recreate="always") as batch:
            batch.drop_constraint("ck_tracking_runs_exactly_one_source", type_="check")
            batch.drop_constraint("fk_tracking_runs_tracked_listing", type_="foreignkey")
            batch.drop_column("tracked_listing_id")
            batch.create_check_constraint(
                "ck_tracking_runs_exactly_one_source",
                "(profile_id IS NOT NULL AND tracked_search_id IS NULL) OR "
                "(profile_id IS NULL AND tracked_search_id IS NOT NULL)",
            )
    op.drop_index("ix_tracked_listings_enabled_last_run", table_name="tracked_listings")
    op.drop_table("tracked_listings")


def _rebuild_sqlite_tracking_runs(*, downgrade: bool = False) -> None:
    bind = op.get_bind()
    source_columns = [
        "id",
        "profile_id",
        "tracked_search_id",
        "started_at",
        "finished_at",
        "status",
        "items_fetched",
        "pages_fetched",
        "profile_ok",
        "stats_ok",
        "reviews_ok",
        "items_ok",
        "error_type",
        "error_message",
        "idempotency_key",
        "matched_listings",
        "new_listings",
        "price_changes",
        "duplicates_suppressed",
    ]
    target_columns = source_columns if downgrade else [
        *source_columns[:3],
        "tracked_listing_id",
        *source_columns[3:],
    ]
    source_sql = ", ".join(source_columns)
    target_sql = ", ".join(target_columns)
    exact_one = (
        "(profile_id IS NOT NULL AND tracked_search_id IS NULL) OR "
        "(profile_id IS NULL AND tracked_search_id IS NOT NULL)"
        if downgrade
        else "((profile_id IS NOT NULL) + (tracked_search_id IS NOT NULL) + "
        "(tracked_listing_id IS NOT NULL)) = 1"
    )
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql(
        f"""CREATE TABLE tracking_runs_new (
        id INTEGER NOT NULL PRIMARY KEY,
        profile_id INTEGER REFERENCES profiles (id),
        tracked_search_id INTEGER REFERENCES tracked_searches (id),
        {'' if downgrade else 'tracked_listing_id INTEGER REFERENCES tracked_listings (id),'}
        started_at DATETIME NOT NULL,
        finished_at DATETIME,
        status VARCHAR(20) NOT NULL,
        items_fetched INTEGER,
        pages_fetched INTEGER,
        profile_ok BOOLEAN NOT NULL,
        stats_ok BOOLEAN NOT NULL,
        reviews_ok BOOLEAN NOT NULL,
        items_ok BOOLEAN NOT NULL,
        error_type VARCHAR(255),
        error_message TEXT,
        idempotency_key VARCHAR(255) UNIQUE,
        matched_listings INTEGER,
        new_listings INTEGER,
        price_changes INTEGER,
        duplicates_suppressed INTEGER,
        CONSTRAINT ck_tracking_runs_exactly_one_source CHECK ({exact_one}),
        CONSTRAINT ck_tracking_runs_status CHECK (
            status IN ('running', 'valid', 'partial', 'failed')
        ),
        CONSTRAINT ck_tracking_runs_finished_at CHECK (
            (status = 'running' AND finished_at IS NULL) OR
            (status <> 'running' AND finished_at IS NOT NULL)
        )
        )"""
    )
    if downgrade:
        copy_sql = (
            f"INSERT INTO tracking_runs_new ({target_sql}) "
            f"SELECT {source_sql} FROM tracking_runs"
        )
    else:
        copy_sql = (
            f"INSERT INTO tracking_runs_new ({target_sql}) "
            "SELECT id, profile_id, tracked_search_id, NULL, started_at, finished_at, "
            "status, items_fetched, pages_fetched, profile_ok, stats_ok, reviews_ok, "
            "items_ok, error_type, error_message, idempotency_key, matched_listings, "
            "new_listings, price_changes, duplicates_suppressed FROM tracking_runs"
        )
    bind.exec_driver_sql(copy_sql)
    bind.exec_driver_sql("DROP TABLE tracking_runs")
    bind.exec_driver_sql("ALTER TABLE tracking_runs_new RENAME TO tracking_runs")
    bind.exec_driver_sql(
        "CREATE INDEX ix_tracking_runs_profile_started "
        "ON tracking_runs (profile_id, started_at)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX ix_tracking_runs_status_finished "
        "ON tracking_runs (status, finished_at)"
    )
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")
