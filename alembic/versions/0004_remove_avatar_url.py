"""Remove the unused profile avatar URL column."""

from sqlalchemy import Column, String, inspect

from alembic import op

revision = "0004_remove_avatar_url"
down_revision = "0003_profile_and_listing_field_extension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("profiles")}
    if "avatar_url" in columns:
        with op.batch_alter_table("profiles") as batch:
            batch.drop_column("avatar_url")


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("profiles")}
    if "avatar_url" not in columns:
        with op.batch_alter_table("profiles") as batch:
            batch.add_column(Column("avatar_url", String(2048)))
