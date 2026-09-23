"""Add explicit marketplace identity to searches and listings."""

import sqlalchemy as sa

from alembic import op

revision = "0013_marketplace_identity"
down_revision = "0012_possible_relistings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    search_columns = {column["name"] for column in inspector.get_columns("tracked_searches")}
    if "marketplace" not in search_columns:
        op.add_column(
            "tracked_searches",
            sa.Column("marketplace", sa.String(32), nullable=False, server_default="wallapop"),
        )

    listing_columns = {column["name"] for column in inspector.get_columns("listings")}
    if "external_id" not in listing_columns:
        legacy = sa.Table(
            "listings",
            sa.MetaData(),
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("wallapop_item_id", sa.String(100)),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id")),
            sa.Column("seller_user_id", sa.String(100)),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        with op.batch_alter_table("listings", recreate="always", copy_from=legacy) as batch:
            batch.add_column(sa.Column("external_id", sa.String(100), nullable=True))
            if "marketplace" not in listing_columns:
                batch.add_column(
                    sa.Column(
                        "marketplace", sa.String(32), nullable=False, server_default="wallapop"
                    )
                )
        op.execute(
            sa.text("UPDATE listings SET external_id = wallapop_item_id WHERE external_id IS NULL")
        )
        op.create_unique_constraint(
            "uq_listings_marketplace_external_id", "listings", ["marketplace", "external_id"]
        )
    if "external_id" in listing_columns and "wallapop_item_id" in listing_columns:
        op.execute(
            sa.text(
                "UPDATE listings SET external_id = wallapop_item_id "
                "WHERE external_id IS NULL AND wallapop_item_id IS NOT NULL"
            )
        )
        existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("listings")}
        if "ix_listings_profile_last_seen" not in existing_indexes:
            op.create_index(
                "ix_listings_profile_last_seen", "listings", ["profile_id", "last_seen_at"]
            )
        if "ix_listings_seller_last_seen" not in existing_indexes:
            op.create_index(
                "ix_listings_seller_last_seen", "listings", ["seller_user_id", "last_seen_at"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "external_id" in {column["name"] for column in inspector.get_columns("listings")}:
        with op.batch_alter_table("listings", recreate="always") as batch:
            batch.drop_constraint("uq_listings_marketplace_external_id", type_="unique")
            batch.drop_column("external_id")
            if "marketplace" in {column["name"] for column in inspector.get_columns("listings")}:
                batch.drop_column("marketplace")
    if "marketplace" in {column["name"] for column in inspector.get_columns("tracked_searches")}:
        op.drop_column("tracked_searches", "marketplace")
