"""Add persistent alert rules and rule-aware delivery idempotency."""
import sqlalchemy as sa

from alembic import op

revision = "0018_alert_rules"
down_revision = "0017_schema_drift_events"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("destination", sa.String(2048), nullable=False),
        sa.Column("filters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))

def downgrade() -> None:
    op.drop_table("alert_rules")
