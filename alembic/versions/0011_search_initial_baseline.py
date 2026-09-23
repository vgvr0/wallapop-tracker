"""Add per-search initial notification policy."""

import sqlalchemy as sa

from alembic import op

revision = "0011_search_initial_baseline"
down_revision = "0010_tracked_listings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing searches have already been part of the active system. Preserve
    # their historical first-run behavior; new ORM-created rows use false.
    op.add_column(
        "tracked_searches",
        sa.Column("notify_on_first_run", sa.Boolean(), nullable=False, server_default="1"),
    )
    op.execute(sa.text("UPDATE tracked_searches SET notify_on_first_run = TRUE"))
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "tracked_searches",
            "notify_on_first_run",
            server_default=sa.text("false"),
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )
    else:
        with op.batch_alter_table("tracked_searches", recreate="always") as batch:
            batch.alter_column(
                "notify_on_first_run",
                server_default="0",
                existing_type=sa.Boolean(),
                existing_nullable=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("tracked_searches", recreate="always") as batch:
        batch.drop_column("notify_on_first_run")
