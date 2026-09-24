"""Persist explicit geolocation on tracked searches."""

import sqlalchemy as sa

from alembic import op

revision = "0027_explicit_search_location"
down_revision = "0026_telegram_control_bot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tracked_searches") as batch:
        batch.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        batch.add_column(sa.Column("max_distance_km", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "ck_tracked_search_latitude", "latitude IS NULL OR latitude BETWEEN -90 AND 90"
        )
        batch.create_check_constraint(
            "ck_tracked_search_longitude", "longitude IS NULL OR longitude BETWEEN -180 AND 180"
        )
        batch.create_check_constraint(
            "ck_tracked_search_distance", "max_distance_km IS NULL OR max_distance_km > 0"
        )
        batch.create_check_constraint(
            "ck_tracked_search_coordinates_pair", "(latitude IS NULL) = (longitude IS NULL)"
        )
        batch.create_check_constraint(
            "ck_tracked_search_distance_coordinates",
            "max_distance_km IS NULL OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("tracked_searches") as batch:
        for name in (
            "ck_tracked_search_distance_coordinates",
            "ck_tracked_search_coordinates_pair",
            "ck_tracked_search_distance",
            "ck_tracked_search_longitude",
            "ck_tracked_search_latitude",
        ):
            batch.drop_constraint(name, type_="check")
        batch.drop_column("max_distance_km")
        batch.drop_column("longitude")
        batch.drop_column("latitude")
