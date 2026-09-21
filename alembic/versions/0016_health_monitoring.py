"""Add run health metrics and schema observations."""

import sqlalchemy as sa

from alembic import op

revision = "0016_health_monitoring"
down_revision = "0015_listing_condition_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("tracking_runs")}
    definitions = {
        "health_status": sa.String(20),
        "duration_ms": sa.Integer(),
        "items_scanned": sa.Integer(),
        "items_new": sa.Integer(),
        "items_changed": sa.Integer(),
        "items_missing": sa.Integer(),
        "items_sold": sa.Integer(),
        "http_requests": sa.Integer(),
        "http_errors": sa.Integer(),
        "http_403": sa.Integer(),
        "http_429": sa.Integer(),
        "http_5xx": sa.Integer(),
        "parse_errors": sa.Integer(),
        "suspicious_result": sa.Boolean(),
    }
    for name, column in definitions.items():
        if name not in cols:
            op.add_column("tracking_runs", sa.Column(name, column, nullable=True))
    op.create_table(
        "schema_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("signature", sa.String(128), nullable=False),
        sa.Column("paths_json", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", name="uq_schema_observations_source"),
    )


def downgrade() -> None:
    op.drop_table("schema_observations")
    for name in (
        "suspicious_result",
        "parse_errors",
        "http_5xx",
        "http_429",
        "http_403",
        "http_errors",
        "http_requests",
        "items_sold",
        "items_missing",
        "items_changed",
        "items_new",
        "items_scanned",
        "duration_ms",
        "health_status",
    ):
        op.drop_column("tracking_runs", name)
