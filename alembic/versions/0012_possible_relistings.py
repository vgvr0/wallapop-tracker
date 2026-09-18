"""Persist explainable heuristic relisting candidates."""

import sqlalchemy as sa

from alembic import op

revision = "0012_possible_relistings"
down_revision = "0011_search_initial_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    listing_columns = {column["name"] for column in inspector.get_columns("listings")}
    if "seller_user_id" not in listing_columns:
        op.add_column("listings", sa.Column("seller_user_id", sa.String(100)))
    listing_indexes = {index["name"] for index in inspector.get_indexes("listings")}
    if "ix_listings_seller_last_seen" not in listing_indexes:
        op.create_index(
            "ix_listings_seller_last_seen", "listings", ["seller_user_id", "last_seen_at"]
        )
    op.create_table(
        "possible_relistings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "previous_listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "current_listing_id",
            sa.Integer(),
            sa.ForeignKey("listings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="candidate"),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("tracking_events.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.UniqueConstraint(
            "previous_listing_id", "current_listing_id", name="uq_possible_relisting_pair"
        ),
    )
    op.create_index(
        "ix_possible_relistings_score_detected",
        "possible_relistings",
        ["score", "detected_at"],
    )
    op.create_index(
        "ix_possible_relistings_current", "possible_relistings", ["current_listing_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_possible_relistings_current", table_name="possible_relistings")
    op.drop_index("ix_possible_relistings_score_detected", table_name="possible_relistings")
    op.drop_table("possible_relistings")
    op.drop_index("ix_listings_seller_last_seen", table_name="listings")
    op.drop_column("listings", "seller_user_id")
