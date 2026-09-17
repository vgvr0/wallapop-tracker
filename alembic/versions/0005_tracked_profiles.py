"""add tracked profile configuration

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_tracked_profiles"
down_revision = "0004_remove_avatar_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_url", sa.String(2048), nullable=False),
        sa.Column("wallapop_user_id", sa.String(100), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id")),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_status", sa.String(20)),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("alias", name="uq_tracked_profiles_alias"),
        sa.UniqueConstraint("profile_url", name="uq_tracked_profiles_url"),
        sa.UniqueConstraint("wallapop_user_id", name="uq_tracked_profiles_user_id"),
    )


def downgrade() -> None:
    op.drop_table("tracked_profiles")
