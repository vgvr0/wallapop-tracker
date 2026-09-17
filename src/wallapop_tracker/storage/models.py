"""SQLAlchemy 2 persistence models.

These ORM models intentionally remain separate from the extraction Pydantic
models. No model here performs HTTP or historical diffing.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class TrackingRunStatus(StrEnum):
    RUNNING = "running"
    VALID = "valid"
    PARTIAL = "partial"
    FAILED = "failed"


class PresenceState(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class Base(DeclarativeBase):
    """Declarative base for the storage schema."""


class SavedSearchRecord(Base):
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
    __tablename__ = "saved_search_items"
    __table_args__ = (
        UniqueConstraint("saved_search_id", "wallapop_item_id", name="uq_saved_search_item"),
    )
    saved_search_id: Mapped[int] = mapped_column(ForeignKey("saved_searches.id"), primary_key=True)
    wallapop_item_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    search: Mapped[SavedSearchRecord] = relationship(back_populates="items")


class PriceWatchRecord(Base):
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
    notes: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[ProfileRecord | None] = relationship()


class TrackingRunRecord(Base):
    __tablename__ = "tracking_runs"
    __table_args__ = (
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
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
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
    profile: Mapped[ProfileRecord] = relationship(back_populates="tracking_runs")
    profile_snapshots: Mapped[list["ProfileSnapshotRecord"]] = relationship(
        back_populates="tracking_run"
    )
    listing_snapshots: Mapped[list["ListingSnapshotRecord"]] = relationship(
        back_populates="tracking_run"
    )


class ListingRecord(Base):
    __tablename__ = "listings"
    __table_args__ = (Index("ix_listings_profile_last_seen", "profile_id", "last_seen_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallapop_item_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    profile: Mapped[ProfileRecord] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshotRecord"]] = relationship(back_populates="listing")


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
        CheckConstraint("reports_count IS NULL OR reports_count >= 0", name="ck_profile_reports"),
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
    reports_count: Mapped[int | None] = mapped_column(Integer)
    rating_1_pct: Mapped[int | None] = mapped_column(Integer)
    rating_2_pct: Mapped[int | None] = mapped_column(Integer)
    rating_3_pct: Mapped[int | None] = mapped_column(Integer)
    rating_4_pct: Mapped[int | None] = mapped_column(Integer)
    rating_5_pct: Mapped[int | None] = mapped_column(Integer)
    profile: Mapped[ProfileRecord] = relationship(back_populates="snapshots")
    tracking_run: Mapped[TrackingRunRecord] = relationship(back_populates="profile_snapshots")


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
    brand: Mapped[str | None] = mapped_column(String(255))
    has_warranty: Mapped[bool | None] = mapped_column()
    is_refurbished: Mapped[bool | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(50))
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
