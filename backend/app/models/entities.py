"""ORM models for the Fortuner buying agent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class ListingStatus(str, Enum):
    ACTIVE = "active"
    POSSIBLY_REMOVED = "possibly_removed"
    REMOVED = "removed"
    RELISTED = "relisted"


class ShortlistStatus(str, Enum):
    WATCHING = "watching"
    INTERESTED = "interested"
    DEALER_CONTACTED = "dealer_contacted"
    VIEWING_SCHEDULED = "viewing_scheduled"
    NEGOTIATING = "negotiating"
    REJECTED = "rejected"
    SOLD = "sold"
    PURCHASED = "purchased"


class MotivationLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class MatchConfidence(str, Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    SEPARATE = "separate"


class SourceListing(Base):
    __tablename__ = "source_listings"
    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_source_listing"),
        Index("ix_source_listings_status", "listing_status"),
        Index("ix_source_listings_canonical", "canonical_vehicle_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    dealer_name: Mapped[str | None] = mapped_column(String(255))
    dealer_phone: Mapped[str | None] = mapped_column(String(64))
    dealer_location: Mapped[str | None] = mapped_column(String(255))
    dealer_stock_number: Mapped[str | None] = mapped_column(String(128))
    year: Mapped[int | None] = mapped_column(Integer)
    make: Mapped[str] = mapped_column(String(64), default="Toyota")
    model: Mapped[str] = mapped_column(String(64), default="Fortuner")
    variant_raw: Mapped[str | None] = mapped_column(String(255))
    variant_normalised: Mapped[str | None] = mapped_column(String(255))
    engine: Mapped[str | None] = mapped_column(String(64))
    transmission: Mapped[str | None] = mapped_column(String(64))
    drivetrain: Mapped[str | None] = mapped_column(String(32))
    fuel_type: Mapped[str | None] = mapped_column(String(32))
    trim: Mapped[str | None] = mapped_column(String(64))
    special_edition: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[str | None] = mapped_column(String(64))
    price_zar: Mapped[int | None] = mapped_column(Integer)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    colour: Mapped[str | None] = mapped_column(String(64))
    vin: Mapped[str | None] = mapped_column(String(32), index=True)
    registration: Mapped[str | None] = mapped_column(String(32), index=True)
    image_urls: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    image_phashes: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    is_stretch_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_flags: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    listing_status: Mapped[str] = mapped_column(
        String(32), default=ListingStatus.ACTIVE.value
    )
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_detail_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_vehicles.id"), nullable=True
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    canonical_vehicle: Mapped[CanonicalVehicle | None] = relationship(
        back_populates="source_listings"
    )
    observations: Mapped[list[ListingObservation]] = relationship(
        back_populates="source_listing", cascade="all, delete-orphan"
    )


class ListingObservation(Base):
    __tablename__ = "listing_observations"
    __table_args__ = (
        Index("ix_observations_listing_time", "source_listing_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_listing_id: Mapped[int] = mapped_column(
        ForeignKey("source_listings.id"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    price_zar: Mapped[int | None] = mapped_column(Integer)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    dealer_name: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(64))
    availability_status: Mapped[str] = mapped_column(String(32))
    image_urls: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    changed_fields: Mapped[list[Any] | None] = mapped_column(JSON, default=list)

    source_listing: Mapped[SourceListing] = relationship(back_populates="observations")


class CanonicalVehicle(Base):
    __tablename__ = "canonical_vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int | None] = mapped_column(Integer)
    make: Mapped[str] = mapped_column(String(64), default="Toyota")
    model: Mapped[str] = mapped_column(String(64), default="Fortuner")
    variant_normalised: Mapped[str | None] = mapped_column(String(255))
    engine: Mapped[str | None] = mapped_column(String(64))
    transmission: Mapped[str | None] = mapped_column(String(64))
    drivetrain: Mapped[str | None] = mapped_column(String(32))
    fuel_type: Mapped[str | None] = mapped_column(String(32))
    trim: Mapped[str | None] = mapped_column(String(64))
    special_edition: Mapped[str | None] = mapped_column(String(64))
    generation: Mapped[str | None] = mapped_column(String(64))
    colour: Mapped[str | None] = mapped_column(String(64))
    vin: Mapped[str | None] = mapped_column(String(32), index=True)
    registration: Mapped[str | None] = mapped_column(String(32), index=True)
    current_lowest_price: Mapped[int | None] = mapped_column(Integer)
    lowest_observed_price: Mapped[int | None] = mapped_column(Integer)
    original_price: Mapped[int | None] = mapped_column(Integer)
    current_mileage_km: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    days_tracked: Mapped[int] = mapped_column(Integer, default=0)
    total_reduction_zar: Mapped[int] = mapped_column(Integer, default=0)
    last_reduction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deal_score: Mapped[float | None] = mapped_column(Float)
    deal_score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    motivation_score: Mapped[float | None] = mapped_column(Float)
    motivation_level: Mapped[str | None] = mapped_column(String(32))
    motivation_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    risk_flags: Mapped[list[Any] | None] = mapped_column(JSON, default=list)
    is_stretch_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    duplicate_match_confidence: Mapped[str | None] = mapped_column(String(32))
    primary_image_url: Mapped[str | None] = mapped_column(Text)
    primary_location: Mapped[str | None] = mapped_column(String(255))
    primary_dealer: Mapped[str | None] = mapped_column(String(255))
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    comparable_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    manual_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    source_listings: Mapped[list[SourceListing]] = relationship(
        back_populates="canonical_vehicle"
    )
    price_events: Mapped[list[PriceEvent]] = relationship(
        back_populates="canonical_vehicle", cascade="all, delete-orphan"
    )
    shortlist_entry: Mapped[ShortlistEntry | None] = relationship(
        back_populates="canonical_vehicle", uselist=False
    )
    match_evidence: Mapped[list[DuplicateMatchEvidence]] = relationship(
        back_populates="canonical_vehicle", cascade="all, delete-orphan"
    )


class PriceEvent(Base):
    __tablename__ = "price_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_vehicles.id"), nullable=False, index=True
    )
    source_listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_listings.id")
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    old_price_zar: Mapped[int | None] = mapped_column(Integer)
    new_price_zar: Mapped[int] = mapped_column(Integer)
    change_zar: Mapped[int] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)

    canonical_vehicle: Mapped[CanonicalVehicle] = relationship(
        back_populates="price_events"
    )


class DuplicateMatchEvidence(Base):
    __tablename__ = "duplicate_match_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_vehicles.id"), nullable=False
    )
    listing_a_id: Mapped[int] = mapped_column(ForeignKey("source_listings.id"))
    listing_b_id: Mapped[int] = mapped_column(ForeignKey("source_listings.id"))
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    canonical_vehicle: Mapped[CanonicalVehicle] = relationship(
        back_populates="match_evidence"
    )


class ShortlistEntry(Base):
    __tablename__ = "shortlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_vehicles.id"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default=ShortlistStatus.WATCHING.value
    )
    interest_level: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    notes: Mapped[str | None] = mapped_column(Text)
    dealer_contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    dealer_response: Mapped[str | None] = mapped_column(Text)
    offered_price_zar: Mapped[int | None] = mapped_column(Integer)
    counteroffer_zar: Mapped[int | None] = mapped_column(Integer)
    viewing_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    draft_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    canonical_vehicle: Mapped[CanonicalVehicle] = relationship(
        back_populates="shortlist_entry"
    )


class CollectorRun(Base):
    __tablename__ = "collector_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    listings_found: Mapped[int] = mapped_column(Integer, default=0)
    listings_new: Mapped[int] = mapped_column(Integer, default=0)
    listings_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    parser_broken: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_snapshot_path: Mapped[str | None] = mapped_column(Text)
    criteria: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SearchProfile(Base):
    """Single active collect/browse criteria set (seeded from Settings)."""

    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="Active search")
    hunt_key: Mapped[str] = mapped_column(String(64), default="fortuner-4x4", index=True)
    make: Mapped[str] = mapped_column(String(64), default="Toyota")
    model: Mapped[str] = mapped_column(String(64), default="Fortuner")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    province: Mapped[str | None] = mapped_column(String(128))
    max_mileage_km: Mapped[int] = mapped_column(Integer, default=100_000)
    required_drivetrain: Mapped[str | None] = mapped_column(String(16))
    required_fuel: Mapped[str | None] = mapped_column(String(32))
    max_price_zar: Mapped[int | None] = mapped_column(Integer)
    enforce_max_price: Mapped[bool] = mapped_column(Boolean, default=False)
    criteria_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    label: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    canonical_vehicle_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_vehicles.id")
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(255), index=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), default="buyer")
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    active_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    reduction_count: Mapped[int] = mapped_column(Integer, default=0)
    median_price: Mapped[int | None] = mapped_column(Integer)
    mean_price: Mapped[float | None] = mapped_column(Float)
    median_mileage: Mapped[int | None] = mapped_column(Integer)
    median_days_on_market: Mapped[float | None] = mapped_column(Float)
    variant_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    criteria_hash: Mapped[str | None] = mapped_column(String(32), index=True)
