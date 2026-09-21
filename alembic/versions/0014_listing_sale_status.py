"""Persist explicit Wallapop sale status in listing snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0014_listing_sale_status"
down_revision = "0013_marketplace_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("listing_snapshots")}
    if "sale_status" not in columns:
        op.add_column(
            "listing_snapshots",
            sa.Column("sale_status", sa.String(20), nullable=False, server_default="unknown"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "sale_status" in {column["name"] for column in inspector.get_columns("listing_snapshots")}:
        op.drop_column("listing_snapshots", "sale_status")
