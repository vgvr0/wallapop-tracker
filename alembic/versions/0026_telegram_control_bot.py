"""Persist Telegram chats and search ownership separately from searches."""

import sqlalchemy as sa

from alembic import op

revision = "0026_telegram_control_bot"
down_revision = "0025_listing_ai_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", name="uq_telegram_chats_chat_id"),
    )
    op.create_table(
        "telegram_search_owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.Integer(), nullable=False),
        sa.Column("tracked_search_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["telegram_chat_id"], ["telegram_chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tracked_search_id"], ["tracked_searches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_chat_id", "tracked_search_id", name="uq_telegram_search_owner"
        ),
    )
    op.create_index(
        "ix_telegram_search_owners_chat", "telegram_search_owners", ["telegram_chat_id"]
    )
    op.create_index(
        "ix_telegram_search_owners_search", "telegram_search_owners", ["tracked_search_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_search_owners_search", table_name="telegram_search_owners")
    op.drop_index("ix_telegram_search_owners_chat", table_name="telegram_search_owners")
    op.drop_table("telegram_search_owners")
    op.drop_table("telegram_chats")
