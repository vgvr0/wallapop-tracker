"""Add the PostgreSQL-native domain event bus, consumers and DLQ."""

import sqlalchemy as sa

from alembic import op

revision = "0023_postgres_event_bus_dlq"
down_revision = "0022_distributed_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "domain_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(255), nullable=False),
        sa.Column("marketplace", sa.String(32)),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("causation_id", sa.String(128)),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_domain_events_idempotency"),
    )
    op.create_index("ix_domain_events_order", "domain_events", ["created_at", "id"])
    op.create_index("ix_domain_events_type_created", "domain_events", ["event_type", "created_at"])
    op.create_index("ix_domain_events_correlation", "domain_events", ["correlation_id"])
    op.create_table(
        "event_consumptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("domain_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("consumer_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("event_id", "consumer_name", name="uq_event_consumption_target"),
    )
    op.create_index(
        "ix_event_consumptions_due",
        "event_consumptions",
        ["consumer_name", "status", "next_attempt_at"],
    )
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("domain_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("consumer_name", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requeued_at", sa.DateTime(timezone=True)),
        sa.Column("requeue_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_dead_letters_consumer_failed", "dead_letters", ["consumer_name", "failed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_dead_letters_consumer_failed", table_name="dead_letters")
    op.drop_table("dead_letters")
    op.drop_index("ix_event_consumptions_due", table_name="event_consumptions")
    op.drop_table("event_consumptions")
    op.drop_index("ix_domain_events_correlation", table_name="domain_events")
    op.drop_index("ix_domain_events_type_created", table_name="domain_events")
    op.drop_index("ix_domain_events_order", table_name="domain_events")
    op.drop_table("domain_events")
