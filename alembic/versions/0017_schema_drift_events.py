"""Persist idempotent schema drift transitions."""

import sqlalchemy as sa

from alembic import op

revision = "0017_schema_drift_events"
down_revision = "0016_health_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schema_drift_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("previous_signature", sa.String(128), nullable=False),
        sa.Column("current_signature", sa.String(128), nullable=False),
        sa.Column("missing_paths_json", sa.Text(), nullable=False),
        sa.Column("new_paths_json", sa.Text(), nullable=False),
        sa.Column("changed_types_json", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source", "previous_signature", "current_signature", name="uq_schema_drift_transition"
        ),
    )


def downgrade() -> None:
    op.drop_table("schema_drift_events")
