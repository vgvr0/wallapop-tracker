"""Add durable notification deliveries."""

import sqlalchemy as sa

from alembic import op

revision = "0009_notification_deliveries"
down_revision = "0008_separate_tracking_run_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("tracking_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("destination", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "event_id", "channel", "destination", name="uq_notification_delivery_target"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_status_created",
        "notification_deliveries",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_status_created",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
