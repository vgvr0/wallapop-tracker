"""Persist Wallapop condition codes and labels separately."""

import sqlalchemy as sa

from alembic import op

revision = "0015_listing_condition_codes"
down_revision = "0014_listing_sale_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("listings", "listing_snapshots"):
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        if "condition_code" not in columns:
            op.add_column(table, sa.Column("condition_code", sa.String(100), nullable=True))
        if "condition_label" not in columns:
            op.add_column(table, sa.Column("condition_label", sa.String(255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("listing_snapshots", "listings"):
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        if "condition_label" in columns:
            op.drop_column(table, "condition_label")
        if "condition_code" in columns:
            op.drop_column(table, "condition_code")
