"""Rename the profile report counter to its public metric name."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0024_reports_received"
down_revision = "0023_postgres_event_bus_dlq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("profile_snapshots")}
    if "reports_count" in columns and "reports_received" not in columns:
        with op.batch_alter_table("profile_snapshots") as batch:
            batch.alter_column("reports_count", new_column_name="reports_received")
    elif "reports_received" not in columns:
        with op.batch_alter_table("profile_snapshots") as batch:
            batch.add_column(sa.Column("reports_received", sa.Integer))


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("profile_snapshots")}
    if "reports_received" in columns and "reports_count" not in columns:
        with op.batch_alter_table("profile_snapshots") as batch:
            batch.alter_column("reports_received", new_column_name="reports_count")
