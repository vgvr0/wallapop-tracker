"""Add explicit presence state and listing lifecycle timestamps.

The conditional checks keep this migration safe for databases created by the
early metadata-based 0001 migration, which may already contain the columns.
"""

from sqlalchemy import Column, DateTime, String, inspect

from alembic import op

revision = "0002_presence_state_and_listing_timestamps"
down_revision = "0001_initial_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    listing_columns = {column["name"] for column in inspector.get_columns("listings")}
    snapshot_columns = {column["name"] for column in inspector.get_columns("listing_snapshots")}
    added_created = "created_at" not in listing_columns
    added_updated = "updated_at" not in listing_columns
    if added_created:
        op.add_column("listings", Column("created_at", DateTime(timezone=True), nullable=True))
    if added_updated:
        op.add_column("listings", Column("updated_at", DateTime(timezone=True), nullable=True))
    if added_created or added_updated:
        op.execute(
            "UPDATE listings SET created_at = COALESCE(created_at, last_seen_at), "
            "updated_at = COALESCE(updated_at, last_seen_at)"
        )
        with op.batch_alter_table("listings") as batch:
            batch.alter_column("created_at", existing_type=DateTime(timezone=True), nullable=False)
            batch.alter_column("updated_at", existing_type=DateTime(timezone=True), nullable=False)
    if "presence_state" not in snapshot_columns:
        op.add_column(
            "listing_snapshots",
            Column("presence_state", String(20), nullable=False, server_default="active"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    snapshot_columns = {column["name"] for column in inspector.get_columns("listing_snapshots")}
    if "presence_state" in snapshot_columns:
        op.drop_column("listing_snapshots", "presence_state")
    listing_columns = {column["name"] for column in inspector.get_columns("listings")}
    if "updated_at" in listing_columns:
        op.drop_column("listings", "updated_at")
    if "created_at" in listing_columns:
        op.drop_column("listings", "created_at")
