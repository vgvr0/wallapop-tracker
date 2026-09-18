"""Separate profile and search tracking-run sources.

Existing search runs may point at a synthetic ``tracked-search:<id>`` profile.
The data cleanup runs before the source constraint is installed, then removes
synthetic profiles only when no valid reference remains.
"""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0008_separate_tracking_run_sources"
down_revision = "0007_search_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both columns must be nullable before historical search runs can lose the
    # synthetic profile reference. The foreign key and CHECK are added only
    # after that data transition is complete.
    existing_checks = {
        constraint.get("name")
        for constraint in inspect(op.get_bind()).get_check_constraints("tracking_runs")
    }
    with op.batch_alter_table("tracking_runs") as batch:
        if "ck_tracking_runs_exactly_one_source" in existing_checks:
            batch.drop_constraint("ck_tracking_runs_exactly_one_source", type_="check")
        batch.alter_column(
            "profile_id", existing_type=sa.Integer(), nullable=True
        )
    with op.batch_alter_table("listings") as batch:
        batch.alter_column(
            "profile_id", existing_type=sa.Integer(), nullable=True
        )

    bind = op.get_bind()
    synthetic_ids = sa.text(
        "SELECT id FROM profiles WHERE wallapop_user_id LIKE 'tracked-search:%'"
    )
    bind.execute(
        sa.text(
            "UPDATE tracking_runs SET profile_id = NULL "
            "WHERE tracked_search_id IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE listings SET profile_id = NULL "
            "WHERE profile_id IN (" + str(synthetic_ids) + ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tracked_profiles SET profile_id = NULL "
            "WHERE profile_id IN (" + str(synthetic_ids) + ")"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM profiles WHERE wallapop_user_id LIKE 'tracked-search:%' "
            "AND id NOT IN (SELECT profile_id FROM tracking_runs WHERE profile_id IS NOT NULL) "
            "AND id NOT IN (SELECT profile_id FROM listings WHERE profile_id IS NOT NULL) "
            "AND id NOT IN (SELECT profile_id FROM tracked_profiles WHERE profile_id IS NOT NULL)"
        )
    )

    with op.batch_alter_table("tracking_runs") as batch:
        batch.create_foreign_key(
            "fk_tracking_runs_tracked_search",
            "tracked_searches",
            ["tracked_search_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_tracking_runs_exactly_one_source",
            "(profile_id IS NOT NULL AND tracked_search_id IS NULL) OR "
            "(profile_id IS NULL AND tracked_search_id IS NOT NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    null_runs = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM tracking_runs "
            "WHERE profile_id IS NULL OR tracked_search_id IS NOT NULL"
        )
    ).scalar_one()
    null_listings = bind.execute(
        sa.text("SELECT COUNT(*) FROM listings WHERE profile_id IS NULL")
    ).scalar_one()
    if null_runs or null_listings:
        raise RuntimeError(
            "Cannot downgrade while search runs or unanchored listings exist"
        )

    with op.batch_alter_table("tracking_runs") as batch:
        batch.drop_constraint("ck_tracking_runs_exactly_one_source", type_="check")
        batch.drop_constraint("fk_tracking_runs_tracked_search", type_="foreignkey")
        batch.alter_column(
            "profile_id", existing_type=sa.Integer(), nullable=False
        )
    with op.batch_alter_table("listings") as batch:
        batch.alter_column(
            "profile_id", existing_type=sa.Integer(), nullable=False
        )
