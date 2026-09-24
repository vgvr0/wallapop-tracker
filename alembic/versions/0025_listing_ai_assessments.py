"""Persist validated LLM listing assessments."""

import sqlalchemy as sa

from alembic import op

revision = "0025_listing_ai_assessments"
down_revision = "0024_reports_received"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_ai_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("semantic_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("condition_assessment", sa.String(length=32), nullable=False),
        sa.Column("condition_confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("deal_quality", sa.String(length=32), nullable=False),
        sa.Column("deal_confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("analysis_json", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Numeric(12, 3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "listing_id",
            "provider",
            "model",
            "prompt_version",
            "input_hash",
            name="uq_listing_ai_assessment_identity",
        ),
    )
    op.create_index(
        "ix_listing_ai_assessments_listing_created",
        "listing_ai_assessments",
        ["listing_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_listing_ai_assessments_listing_created", table_name="listing_ai_assessments")
    op.drop_table("listing_ai_assessments")
