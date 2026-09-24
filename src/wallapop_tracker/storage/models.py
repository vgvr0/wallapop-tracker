"""SQLAlchemy 2 persistence models.

These ORM models intentionally remain separate from the extraction Pydantic
models. No model here performs HTTP or historical diffing.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, foreign, mapped_column, relationship

from wallapop_tracker.domain.marketplace import Marketplace


class TrackingRunStatus(StrEnum):
    RUNNING = "running"
    VALID = "valid"
    PARTIAL = "partial"
    FAILED = "failed"


class HealthRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class PresenceState(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class ListingSaleStatus(StrEnum):
    UNKNOWN = "unknown"
    ACTIVE = "active"
    RESERVED = "reserved"
    SOLD = "sold"


class Base(DeclarativeBase):
    """Declarative base for the storage schema."""


class SavedSearchRecord(Base):
    """Deprecated pre-TrackedSearch persistence kept for old databases."""

    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[str | None] = mapped_column(String(100))
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    condition: Mapped[str | None] = mapped_column(String(100))
    brand: Mapped[str | None] = mapped_column(String(255))
    shipping_required: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items: Mapped[list["SavedSearchItemRecord"]] = relationship(back_populates="search")


class SavedSearchItemRecord(Base):
    """Deprecated child rows for :class:`SavedSearchRecord`."""

    __tablename__ = "saved_search_items"
    __table_args__ = (
        UniqueConstraint("saved_search_id", "wallapop_item_id", name="uq_saved_search_item"),
    )
    saved_search_id: Mapped[int] = mapped_column(ForeignKey("saved_searches.id"), primary_key=True)
    wallapop_item_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    search: Mapped[SavedSearchRecord] = relationship(back_populates="items")


class TrackedSearchRecord(Base):
    """Persistent configuration for the integrated search tracker."""

    __tablename__ = "tracked_searches"
    __table_args__ = (
        CheckConstraint("interval_seconds > 0", name="ck_tracked_search_interval"),
        CheckConstraint(
            "min_price IS NULL OR max_price IS NULL OR min_price <= max_price",
            name="ck_tracked_search_price_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=Marketplace.WALLAPOP,
        server_default=Marketplace.WALLAPOP,
    )
    name: Mapped[str | None] = mapped_column(String(255))
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    filters_json: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    notify_on_first_run: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    percentage_drop_threshold: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    deal_score_threshold: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    notify_on_30d_low: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    notify_on_90d_low: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    notify_on_all_time_low: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[str | None] = mapped_column(String(20))
    last_run_id: Mapped[int | None] = mapped_column(Integer)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(255))


class TelegramChatRecord(Base):
    """Telegram chat metadata; ``chat_id`` is the canonical identity."""

    __tablename__ = "telegram_chats"
    __table_args__ = (UniqueConstraint("chat_id", name="uq_telegram_chats_chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramSearchOwnerRecord(Base):
    """Ownership association between a Telegram chat and a tracked search."""

    __tablename__ = "telegram_search_owners"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "tracked_search_id", name="uq_telegram_search_owner"),
        Index("ix_telegram_search_owners_chat", "telegram_chat_id"),
        Index("ix_telegram_search_owners_search", "tracked_search_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_chats.id", ondelete="CASCADE"), nullable=False
    )
    tracked_search_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_searches.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrackedListingRecord(Base):
    """Persistent configuration for monitoring one global listing."""

    __tablename__ = "tracked_listings"
    __table_args__ = (
        UniqueConstraint("alias", name="uq_tracked_listings_alias"),
        UniqueConstraint("listing_id", name="uq_tracked_listings_listing"),
        CheckConstraint("interval_seconds > 0", name="ck_tracked_listing_interval"),
        Index("ix_tracked_listings_enabled_last_run", "enabled", "last_run_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[str | None] = mapped_column(String(20))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    last_tracking_run_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    percentage_drop_threshold: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    deal_score_threshold: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    notify_on_30d_low: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    notify_on_90d_low: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    notify_on_all_time_low: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    listing: Mapped["ListingRecord"] = relationship()


class SearchListingMatchRecord(Base):
    """Global listing-to-search association and observation counters."""

    __tablename__ = "search_listing_matches"
    __table_args__ = (
        UniqueConstraint("tracked_search_id", "listing_id", name="uq_search_listing_match"),
        Index("ix_search_listing_matches_listing", "listing_id"),
    )

    tracked_search_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_searches.id"), primary_key=True
    )
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tracked_search: Mapped[TrackedSearchRecord] = relationship()
    listing: Mapped["ListingRecord"] = relationship()


class TrackingEventRecord(Base):
    """Durable idempotency ledger for globally deduplicated tracker events."""

    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tracking_events_idempotency"),
        Index("ix_tracking_events_listing_created", "listing_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    tracking_run_id: Mapped[int] = mapped_column(ForeignKey("tracking_runs.id"), nullable=False)
    tracked_search_id: Mapped[int | None] = mapped_column(ForeignKey("tracked_searches.id"))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text)
    # Deprecated compatibility flag; notification_deliveries is authoritative.
    alert_delivered: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    listing: Mapped["ListingRecord"] = relationship()
    tracking_run: Mapped["TrackingRunRecord"] = relationship()
    tracked_search: Mapped[TrackedSearchRecord | None] = relationship()
    deliveries: Mapped[list["NotificationDeliveryRecord"]] = relationship(back_populates="event")


class DomainEventRecord(Base):
    """Append-only domain event outbox shared by all asynchronous consumers."""

    __tablename__ = "domain_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_domain_events_idempotency"),
        Index("ix_domain_events_order", "created_at", "id"),
        Index("ix_domain_events_type_created", "event_type", "created_at"),
        Index("ix_domain_events_correlation", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    marketplace: Mapped[str | None] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    metadata_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)


class EventConsumptionStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    RETRY = "retry"
    DLQ = "dlq"


class EventConsumptionRecord(Base):
    """Independent delivery state for one event/consumer pair."""

    __tablename__ = "event_consumptions"
    __table_args__ = (
        UniqueConstraint("event_id", "consumer_name", name="uq_event_consumption_target"),
        Index("ix_event_consumptions_due", "consumer_name", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("domain_events.id", ondelete="RESTRICT"), nullable=False
    )
    consumer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[EventConsumptionStatus] = mapped_column(
        String(20), nullable=False, default=EventConsumptionStatus.PENDING, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    event: Mapped[DomainEventRecord] = relationship()


class DeadLetterRecord(Base):
    """Immutable record of a terminal consumer failure."""

    __tablename__ = "dead_letters"
    __table_args__ = (Index("ix_dead_letters_consumer_failed", "consumer_name", "failed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("domain_events.id", ondelete="RESTRICT"), nullable=False
    )
    consumer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requeued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requeue_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    event: Mapped[DomainEventRecord] = relationship()


class DealScoreSnapshotRecord(Base):
    """A score observation scoped to one listing/search context."""

    __tablename__ = "deal_score_snapshots"
    __table_args__ = (
        Index(
            "ix_deal_score_snapshots_context_time", "listing_id", "tracked_search_id", "computed_at"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    tracked_search_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_searches.id"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationDeliveryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class AlertRuleRecord(Base):
    """Persistent, deliberately small event-to-channel rule."""

    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_event_enabled", "event_type", "enabled"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    destination: Mapped[str] = mapped_column(String(2048), nullable=False)
    filters_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationDeliveryRecord(Base):
    """Durable delivery attempt for one event and destination."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "channel",
            "destination",
            name="uq_notification_delivery_target",
        ),
        Index("ix_notification_deliveries_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("tracking_events.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    destination: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        String(20), nullable=False, default=NotificationDeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    event: Mapped[TrackingEventRecord] = relationship(back_populates="deliveries")


class PriceWatchRecord(Base):
    """Deprecated pre-TrackedListing watch retained for historical data."""

    __tablename__ = "price_watches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_notified_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))


class ProfileRecord(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallapop_user_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(2048))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_city: Mapped[str | None] = mapped_column(String(255))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    country_code: Mapped[str | None] = mapped_column(String(8))
    seller_type: Mapped[str | None] = mapped_column(String(100))
    verified: Mapped[bool | None] = mapped_column()
    is_top_profile: Mapped[bool | None] = mapped_column()
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tracking_runs: Mapped[list["TrackingRunRecord"]] = relationship(back_populates="profile")
    listings: Mapped[list["ListingRecord"]] = relationship(back_populates="profile")
    snapshots: Mapped[list["ProfileSnapshotRecord"]] = relationship(back_populates="profile")


class TrackedProfileRecord(Base):
    __tablename__ = "tracked_profiles"
    __table_args__ = (
        UniqueConstraint("alias", name="uq_tracked_profiles_alias"),
        UniqueConstraint("profile_url", name="uq_tracked_profiles_url"),
        UniqueConstraint("wallapop_user_id", name="uq_tracked_profiles_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    wallapop_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[str | None] = mapped_column(String(20))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[ProfileRecord | None] = relationship()


class TrackingRunRecord(Base):
    __tablename__ = "tracking_runs"
    __table_args__ = (
        CheckConstraint(
            "((CASE WHEN profile_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN tracked_search_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN tracked_listing_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="ck_tracking_runs_exactly_one_source",
        ),
        CheckConstraint(
            "status IN ('running', 'valid', 'partial', 'failed')",
            name="ck_tracking_runs_status",
        ),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR "
            "(status <> 'running' AND finished_at IS NOT NULL)",
            name="ck_tracking_runs_finished_at",
        ),
        Index("ix_tracking_runs_profile_started", "profile_id", "started_at"),
        Index("ix_tracking_runs_status_finished", "status", "finished_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"))
    tracked_search_id: Mapped[int | None] = mapped_column(ForeignKey("tracked_searches.id"))
    tracked_listing_id: Mapped[int | None] = mapped_column(ForeignKey("tracked_listings.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[TrackingRunStatus] = mapped_column(
        String(20), nullable=False, default=TrackingRunStatus.RUNNING
    )
    items_fetched: Mapped[int | None] = mapped_column(Integer)
    pages_fetched: Mapped[int | None] = mapped_column(Integer)
    profile_ok: Mapped[bool] = mapped_column(nullable=False, default=False)
    stats_ok: Mapped[bool] = mapped_column(nullable=False, default=False)
    reviews_ok: Mapped[bool] = mapped_column(nullable=False, default=False)
    items_ok: Mapped[bool] = mapped_column(nullable=False, default=False)
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    matched_listings: Mapped[int | None] = mapped_column(Integer)
    new_listings: Mapped[int | None] = mapped_column(Integer)
    price_changes: Mapped[int | None] = mapped_column(Integer)
    duplicates_suppressed: Mapped[int | None] = mapped_column(Integer)
    health_status: Mapped[str | None] = mapped_column(String(20))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    items_scanned: Mapped[int | None] = mapped_column(Integer)
    items_new: Mapped[int | None] = mapped_column(Integer)
    items_changed: Mapped[int | None] = mapped_column(Integer)
    items_missing: Mapped[int | None] = mapped_column(Integer)
    items_sold: Mapped[int | None] = mapped_column(Integer)
    http_requests: Mapped[int | None] = mapped_column(Integer)
    http_errors: Mapped[int | None] = mapped_column(Integer)
    http_403: Mapped[int | None] = mapped_column(Integer)
    http_429: Mapped[int | None] = mapped_column(Integer)
    http_5xx: Mapped[int | None] = mapped_column(Integer)
    parse_errors: Mapped[int | None] = mapped_column(Integer)
    suspicious_result: Mapped[bool | None] = mapped_column()
    profile: Mapped[ProfileRecord | None] = relationship(back_populates="tracking_runs")
    tracked_search: Mapped[TrackedSearchRecord | None] = relationship()
    tracked_listing: Mapped[TrackedListingRecord | None] = relationship(
        primaryjoin=lambda: (
            foreign(TrackingRunRecord.tracked_listing_id) == TrackedListingRecord.id
        ),
        foreign_keys=[tracked_listing_id],
    )
    profile_snapshots: Mapped[list["ProfileSnapshotRecord"]] = relationship(
        back_populates="tracking_run"
    )
    listing_snapshots: Mapped[list["ListingSnapshotRecord"]] = relationship(
        back_populates="tracking_run"
    )


class SchemaObservationRecord(Base):
    """Latest structural fingerprint for a monitored payload source."""

    __tablename__ = "schema_observations"
    __table_args__ = (UniqueConstraint("source", name="uq_schema_observations_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    paths_json: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaDriftEventRecord(Base):
    __tablename__ = "schema_drift_events"
    __table_args__ = (
        UniqueConstraint(
            "source", "previous_signature", "current_signature", name="uq_schema_drift_transition"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    previous_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    current_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    missing_paths_json: Mapped[str] = mapped_column(Text, nullable=False)
    new_paths_json: Mapped[str] = mapped_column(Text, nullable=False)
    changed_types_json: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ListingRecord(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("marketplace", "external_id", name="uq_listings_marketplace_external_id"),
        Index("ix_listings_profile_last_seen", "profile_id", "last_seen_at"),
        Index("ix_listings_seller_last_seen", "seller_user_id", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=Marketplace.WALLAPOP,
        server_default=Marketplace.WALLAPOP,
    )
    # Kept as a physical legacy bridge because revision 0001 is historical
    # and creates its tables from metadata. New code uses external_id.
    wallapop_item_id: Mapped[str | None] = mapped_column(String(100))
    external_id: Mapped[str | None] = mapped_column(String(100), default=None)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"))
    seller_user_id: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    condition_code: Mapped[str | None] = mapped_column(String(100))
    condition_label: Mapped[str | None] = mapped_column(String(255))
    profile: Mapped[ProfileRecord | None] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshotRecord"]] = relationship(back_populates="listing")


class PossibleRelistingStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class PossibleRelistingRecord(Base):
    """Explainable relation between two independent listing identities."""

    __tablename__ = "possible_relistings"
    __table_args__ = (
        UniqueConstraint(
            "previous_listing_id", "current_listing_id", name="uq_possible_relisting_pair"
        ),
        Index("ix_possible_relistings_score_detected", "score", "detected_at"),
        Index("ix_possible_relistings_current", "current_listing_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    previous_listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False
    )
    current_listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="RESTRICT"), nullable=False
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[PossibleRelistingStatus] = mapped_column(
        String(20),
        nullable=False,
        default=PossibleRelistingStatus.CANDIDATE,
        server_default="candidate",
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracking_events.id", ondelete="SET NULL"), unique=True
    )
    previous_listing: Mapped["ListingRecord"] = relationship(foreign_keys=[previous_listing_id])
    current_listing: Mapped["ListingRecord"] = relationship(foreign_keys=[current_listing_id])


class ProfileSnapshotRecord(Base):
    __tablename__ = "profile_snapshots"
    __table_args__ = (
        UniqueConstraint("profile_id", "tracking_run_id", name="uq_profile_snapshot_run"),
        CheckConstraint(
            "rating IS NULL OR (rating >= 0 AND rating <= 5)", name="ck_profile_rating"
        ),
        CheckConstraint("review_count IS NULL OR review_count >= 0", name="ck_profile_reviews"),
        CheckConstraint(
            "published_count IS NULL OR published_count >= 0", name="ck_profile_published"
        ),
        CheckConstraint(
            "purchases_count IS NULL OR purchases_count >= 0", name="ck_profile_purchases"
        ),
        CheckConstraint("sales_count IS NULL OR sales_count >= 0", name="ck_profile_sales"),
        CheckConstraint("sold_count IS NULL OR sold_count >= 0", name="ck_profile_sold"),
        CheckConstraint(
            "reports_received IS NULL OR reports_received >= 0", name="ck_profile_reports"
        ),
        Index("ix_profile_snapshots_profile_observed", "profile_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    tracking_run_id: Mapped[int] = mapped_column(ForeignKey("tracking_runs.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    review_count: Mapped[int | None] = mapped_column(Integer)
    published_count: Mapped[int | None] = mapped_column(Integer)
    purchases_count: Mapped[int | None] = mapped_column(Integer)
    sales_count: Mapped[int | None] = mapped_column(Integer)
    sold_count: Mapped[int | None] = mapped_column(Integer)
    reports_received: Mapped[int | None] = mapped_column(Integer)
    rating_1_pct: Mapped[int | None] = mapped_column(Integer)
    rating_2_pct: Mapped[int | None] = mapped_column(Integer)
    rating_3_pct: Mapped[int | None] = mapped_column(Integer)
    rating_4_pct: Mapped[int | None] = mapped_column(Integer)
    rating_5_pct: Mapped[int | None] = mapped_column(Integer)
    profile: Mapped[ProfileRecord] = relationship(back_populates="snapshots")
    tracking_run: Mapped[TrackingRunRecord] = relationship(back_populates="profile_snapshots")

    @property
    def reports_count(self) -> int | None:
        return self.reports_received


class ListingSnapshotRecord(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "tracking_run_id", name="uq_listing_snapshot_run"),
        CheckConstraint("price IS NULL OR price >= 0", name="ck_listing_price"),
        Index("ix_listing_snapshots_listing_observed", "listing_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    tracking_run_id: Mapped[int] = mapped_column(ForeignKey("tracking_runs.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    category_id: Mapped[str | None] = mapped_column(String(100))
    category_name: Mapped[str | None] = mapped_column(String(255))
    reserved: Mapped[bool | None] = mapped_column()
    shipping_available: Mapped[bool | None] = mapped_column()
    seller_allows_shipping: Mapped[bool | None] = mapped_column()
    condition: Mapped[str | None] = mapped_column(String(100))
    condition_code: Mapped[str | None] = mapped_column(String(100))
    condition_label: Mapped[str | None] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(255))
    has_warranty: Mapped[bool | None] = mapped_column()
    is_refurbished: Mapped[bool | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(50))
    sale_status: Mapped[ListingSaleStatus] = mapped_column(
        String(20), nullable=False, default=ListingSaleStatus.UNKNOWN, server_default="unknown"
    )
    url: Mapped[str | None] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    images_json: Mapped[str | None] = mapped_column(Text)
    attributes_json: Mapped[str | None] = mapped_column(Text)
    presence_state: Mapped[PresenceState] = mapped_column(
        String(20), nullable=False, default=PresenceState.ACTIVE
    )
    raw_json: Mapped[str | None] = mapped_column(Text)
    listing: Mapped[ListingRecord] = relationship(back_populates="snapshots")
    tracking_run: Mapped[TrackingRunRecord] = relationship(back_populates="listing_snapshots")


class ListingAIAssessmentRecord(Base):
    """Canonical validated semantic assessment for one input/model version."""

    __tablename__ = "listing_ai_assessments"
    __table_args__ = (
        UniqueConstraint(
            "listing_id",
            "provider",
            "model",
            "prompt_version",
            "input_hash",
            name="uq_listing_ai_assessment_identity",
        ),
        Index("ix_listing_ai_assessments_listing_created", "listing_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    condition_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    condition_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    deal_quality: Mapped[str] = mapped_column(String(32), nullable=False)
    deal_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    analysis_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    listing: Mapped[ListingRecord] = relationship()


class TrackingRunListingRecord(Base):
    __tablename__ = "tracking_run_listings"
    __table_args__ = (
        UniqueConstraint("tracking_run_id", "listing_id", name="uq_tracking_run_listing"),
        Index("ix_tracking_run_listings_listing", "listing_id"),
    )

    tracking_run_id: Mapped[int] = mapped_column(ForeignKey("tracking_runs.id"), primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tracking_run: Mapped[TrackingRunRecord] = relationship()
    listing: Mapped[ListingRecord] = relationship()
