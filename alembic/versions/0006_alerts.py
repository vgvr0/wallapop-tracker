"""Add saved searches and price watches."""

import sqlalchemy as sa

from alembic import op

revision = "0006_alerts"
down_revision = "0005_tracked_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("category_id", sa.String(100)),
        sa.Column("min_price", sa.Numeric(12, 2)),
        sa.Column("max_price", sa.Numeric(12, 2)),
        sa.Column("condition", sa.String(100)),
        sa.Column("brand", sa.String(255)),
        sa.Column("shipping_required", sa.Boolean),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "saved_search_items",
        sa.Column(
            "saved_search_id", sa.Integer, sa.ForeignKey("saved_searches.id"), primary_key=True
        ),
        sa.Column("wallapop_item_id", sa.String(100), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("saved_search_id", "wallapop_item_id", name="uq_saved_search_item"),
    )
    op.create_table(
        "price_watches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "listing_id", sa.Integer, sa.ForeignKey("listings.id"), unique=True, nullable=False
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_price", sa.Numeric(12, 2)),
    )


def downgrade() -> None:
    op.drop_table("price_watches")
    op.drop_table("saved_search_items")
    op.drop_table("saved_searches")
