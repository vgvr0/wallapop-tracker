"""Persist contextual deal-score observations for threshold crossings."""

import sqlalchemy as sa

from alembic import op

revision = "0021_deal_score_snapshots"
down_revision = "0020_advanced_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deal_score_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=False),
        sa.Column(
            "tracked_search_id", sa.Integer(), sa.ForeignKey("tracked_searches.id"), nullable=False
        ),
        sa.Column("score", sa.Numeric(5, 2), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_deal_score_snapshots_context_time",
        "deal_score_snapshots",
        ["listing_id", "tracked_search_id", "computed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deal_score_snapshots_context_time", table_name="deal_score_snapshots")
    op.drop_table("deal_score_snapshots")
