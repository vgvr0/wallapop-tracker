"""Add durable leases for tracking jobs and notification deliveries."""

import sqlalchemy as sa

from alembic import op

revision = "0022_distributed_leases"
down_revision = "0021_deal_score_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tracked_profiles", "tracked_searches", "tracked_listings"):
        op.add_column(table, sa.Column("claimed_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("claim_expires_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("claimed_by", sa.String(255)))
    op.add_column(
        "notification_deliveries", sa.Column("next_attempt_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "notification_deliveries", sa.Column("processing_started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "notification_deliveries", sa.Column("claim_expires_at", sa.DateTime(timezone=True))
    )
    op.add_column("notification_deliveries", sa.Column("claimed_by", sa.String(255)))
    op.create_index(
        "ix_tracking_claim_expiration", "tracked_profiles", ["enabled", "claim_expires_at"]
    )
    op.create_index(
        "ix_search_claim_expiration", "tracked_searches", ["enabled", "claim_expires_at"]
    )
    op.create_index(
        "ix_listing_claim_expiration", "tracked_listings", ["enabled", "claim_expires_at"]
    )
    op.create_index(
        "ix_notification_due",
        "notification_deliveries",
        ["status", "next_attempt_at", "claim_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_due", table_name="notification_deliveries")
    op.drop_index("ix_listing_claim_expiration", table_name="tracked_listings")
    op.drop_index("ix_search_claim_expiration", table_name="tracked_searches")
    op.drop_index("ix_tracking_claim_expiration", table_name="tracked_profiles")
    for table in ("notification_deliveries",):
        for column in (
            "claimed_by",
            "claim_expires_at",
            "processing_started_at",
            "next_attempt_at",
        ):
            op.drop_column(table, column)
    for table in ("tracked_profiles", "tracked_searches", "tracked_listings"):
        for column in ("claimed_by", "claim_expires_at", "claimed_at"):
            op.drop_column(table, column)
