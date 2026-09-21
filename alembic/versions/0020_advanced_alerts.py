"""Add durable advanced-alert configuration and event explanations."""

import sqlalchemy as sa

from alembic import op

revision = "0020_advanced_alerts"
down_revision = "0019_advanced_search_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tracked_searches", "tracked_listings"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("target_price", sa.Numeric(12, 2)))
            batch.add_column(sa.Column("percentage_drop_threshold", sa.Numeric(5, 2)))
            batch.add_column(sa.Column("deal_score_threshold", sa.Numeric(5, 2)))
            batch.add_column(
                sa.Column("notify_on_30d_low", sa.Boolean(), nullable=False, server_default="0")
            )
            batch.add_column(
                sa.Column("notify_on_90d_low", sa.Boolean(), nullable=False, server_default="0")
            )
            batch.add_column(
                sa.Column(
                    "notify_on_all_time_low", sa.Boolean(), nullable=False, server_default="0"
                )
            )
    with op.batch_alter_table("tracking_events") as batch:
        batch.add_column(sa.Column("metadata_json", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("tracking_events") as batch:
        batch.drop_column("metadata_json")
    for table in ("tracked_listings", "tracked_searches"):
        with op.batch_alter_table(table) as batch:
            for name in (
                "notify_on_all_time_low",
                "notify_on_90d_low",
                "notify_on_30d_low",
                "deal_score_threshold",
                "percentage_drop_threshold",
                "target_price",
            ):
                batch.drop_column(name)
