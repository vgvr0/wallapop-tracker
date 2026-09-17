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
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from wallapop_tracker.storage.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
