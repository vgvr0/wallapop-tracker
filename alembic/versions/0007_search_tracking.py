"""Add integrated tracked-search history and idempotent events."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0007_search_tracking"
down_revision = "0006_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("tracking_runs")}
    op.create_table(
        "tracked_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("min_price", sa.Numeric(12, 2)),
        sa.Column("max_price", sa.Numeric(12, 2)),
        sa.Column("filters_json", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_status", sa.String(20)),
        sa.Column("last_run_id", sa.Integer()),
        sa.CheckConstraint("interval_seconds > 0", name="ck_tracked_search_interval"),
        sa.CheckConstraint(
            "min_price IS NULL OR max_price IS NULL OR min_price <= max_price",
            name="ck_tracked_search_price_range",
        ),
    )
    # SQLite can add nullable columns with references directly. A batch
    # recreation here would see a false circular dependency because the new
    # table stores a non-FK run identifier for display metadata.
    if "tracked_search_id" not in existing:
        op.add_column(
            "tracking_runs",
            sa.Column("tracked_search_id", sa.Integer()),
        )
    for name in ("matched_listings", "new_listings", "price_changes", "duplicates_suppressed"):
        if name not in existing:
            op.add_column("tracking_runs", sa.Column(name, sa.Integer()))
    op.create_table(
        "search_listing_matches",
        sa.Column(
            "tracked_search_id",
            sa.Integer(),
            sa.ForeignKey("tracked_searches.id"),
            primary_key=True,
        ),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detection_count", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tracked_search_id", "listing_id", name="uq_search_listing_match"),
    )
    op.create_index("ix_search_listing_matches_listing", "search_listing_matches", ["listing_id"])
    op.create_table(
        "tracking_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column(
            "tracking_run_id", sa.Integer(), sa.ForeignKey("tracking_runs.id"), nullable=False
        ),
        sa.Column("tracked_search_id", sa.Integer(), sa.ForeignKey("tracked_searches.id")),
        sa.Column("old_price", sa.Numeric(12, 2)),
        sa.Column("new_price", sa.Numeric(12, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alert_delivered", sa.Boolean(), nullable=False, server_default="1"),
        sa.UniqueConstraint("idempotency_key", name="uq_tracking_events_idempotency"),
    )
    op.create_index(
        "ix_tracking_events_listing_created", "tracking_events", ["listing_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tracking_events_listing_created", table_name="tracking_events")
    op.drop_table("tracking_events")
    op.drop_index("ix_search_listing_matches_listing", table_name="search_listing_matches")
    op.drop_table("search_listing_matches")
    with op.batch_alter_table("tracking_runs") as batch:
        batch.drop_column("duplicates_suppressed")
        batch.drop_column("price_changes")
        batch.drop_column("new_listings")
        batch.drop_column("matched_listings")
        batch.drop_column("tracked_search_id")
    op.drop_table("tracked_searches")
