"""create initial historical storage schema

Revision ID: 0001_initial_storage
Revises:
"""

from alembic import op

revision = "0001_initial_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from wallapop_tracker.storage.models import Base

    bind = op.get_bind()
    # This migration must represent the schema that existed at revision 0001.
    # Using the live metadata here would also create tables introduced by later
    # revisions (tracked profiles and alerts) before their own migrations run.
    initial_tables = [
        Base.metadata.tables[name]
        for name in (
            "profiles",
            "tracking_runs",
            "listings",
            "profile_snapshots",
            "listing_snapshots",
            "tracking_run_listings",
        )
    ]
    Base.metadata.create_all(bind=bind, tables=initial_tables)


def downgrade() -> None:
    from wallapop_tracker.storage.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
