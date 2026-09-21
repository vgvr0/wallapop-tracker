"""Document advanced search filters stored in the existing JSON configuration.

No columns are required: filters_json is the backwards-compatible structured
storage boundary, while this revision adds validation constraints for prices.
"""

from alembic import op

revision = "0019_advanced_search_filters"
down_revision = "0018_alert_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tracked_searches") as batch:
        batch.create_check_constraint("ck_tracked_search_min_price_nonnegative", "min_price IS NULL OR min_price >= 0")
        batch.create_check_constraint("ck_tracked_search_max_price_nonnegative", "max_price IS NULL OR max_price >= 0")


def downgrade() -> None:
    with op.batch_alter_table("tracked_searches") as batch:
        batch.drop_constraint("ck_tracked_search_max_price_nonnegative", type_="check")
        batch.drop_constraint("ck_tracked_search_min_price_nonnegative", type_="check")
