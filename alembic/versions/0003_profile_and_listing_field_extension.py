"""Add the minimal profile and listing snapshot field extension."""

from sqlalchemy import Boolean, Column, DateTime, String, Text, inspect

from alembic import op

revision = "0003_profile_and_listing_field_extension"
down_revision = "0002_presence_state_and_listing_timestamps"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    profile_columns = _columns("profiles")
    profile_additions = {
        "registered_at": Column("registered_at", DateTime(timezone=True)),
        "avatar_url": Column("avatar_url", String(2048)),
        "location_city": Column("location_city", String(255)),
        "postal_code": Column("postal_code", String(32)),
        "country_code": Column("country_code", String(8)),
        "seller_type": Column("seller_type", String(100)),
        "verified": Column("verified", Boolean),
        "is_top_profile": Column("is_top_profile", Boolean),
    }
    with op.batch_alter_table("profiles") as batch:
        for name, column in profile_additions.items():
            if name not in profile_columns:
                batch.add_column(column)

    snapshot_columns = _columns("profile_snapshots")
    with op.batch_alter_table("profile_snapshots") as batch:
        for old, new in (
            ("rating_1_count", "rating_1_pct"),
            ("rating_2_count", "rating_2_pct"),
            ("rating_3_count", "rating_3_pct"),
            ("rating_4_count", "rating_4_pct"),
            ("rating_5_count", "rating_5_pct"),
        ):
            if old in snapshot_columns and new not in snapshot_columns:
                batch.alter_column(old, new_column_name=new)
    listing_columns = _columns("listing_snapshots")
    listing_additions = {
        "shipping_available": Column("shipping_available", Boolean),
        "seller_allows_shipping": Column("seller_allows_shipping", Boolean),
        "condition": Column("condition", String(100)),
        "brand": Column("brand", String(255)),
        "has_warranty": Column("has_warranty", Boolean),
        "is_refurbished": Column("is_refurbished", Boolean),
        "images_json": Column("images_json", Text),
        "attributes_json": Column("attributes_json", Text),
    }
    with op.batch_alter_table("listing_snapshots") as batch:
        for name, column in listing_additions.items():
            if name not in listing_columns:
                batch.add_column(column)


def downgrade() -> None:
    listing_columns = _columns("listing_snapshots")
    with op.batch_alter_table("listing_snapshots") as batch:
        for name in (
            "attributes_json",
            "images_json",
            "is_refurbished",
            "has_warranty",
            "brand",
            "condition",
            "seller_allows_shipping",
            "shipping_available",
        ):
            if name in listing_columns:
                batch.drop_column(name)

    snapshot_columns = _columns("profile_snapshots")
    with op.batch_alter_table("profile_snapshots") as batch:
        for old, new in (
            ("rating_1_pct", "rating_1_count"),
            ("rating_2_pct", "rating_2_count"),
            ("rating_3_pct", "rating_3_count"),
            ("rating_4_pct", "rating_4_count"),
            ("rating_5_pct", "rating_5_count"),
        ):
            if old in snapshot_columns and new not in snapshot_columns:
                batch.alter_column(old, new_column_name=new)

    profile_columns = _columns("profiles")
    with op.batch_alter_table("profiles") as batch:
        for name in (
            "is_top_profile",
            "verified",
            "seller_type",
            "country_code",
            "postal_code",
            "location_city",
            "avatar_url",
            "registered_at",
        ):
            if name in profile_columns:
                batch.drop_column(name)
