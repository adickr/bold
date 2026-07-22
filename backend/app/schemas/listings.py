"""Pydantic schemas for API and collector payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ListingPayload(BaseModel):
    """Normalised listing as returned by a collector adapter."""

    source: str
    source_listing_id: str
    url: str
    title: str | None = None
    description: str | None = None
    dealer_name: str | None = None
    dealer_phone: str | None = None
    dealer_location: str | None = None
    dealer_stock_number: str | None = None
    year: int | None = None
    make: str = "Toyota"
    model: str = "Fortuner"
    variant_raw: str | None = None
    price_zar: int | None = None
    mileage_km: int | None = None
    colour: str | None = None
    vin: str | None = None
    registration: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    transmission: str | None = None
    fuel_type: str | None = None
    drivetrain: str | None = None
    # available | unavailable — WeBuyCars "Reserved" / sale-in-progress, etc.
    availability: str | None = "available"
    raw_payload: dict[str, Any] | None = None


class VariantInfo(BaseModel):
    variant_normalised: str
    engine: str | None = None
    transmission: str | None = None
    drivetrain: str | None = None
    fuel_type: str | None = None
    trim: str | None = None
    special_edition: str | None = None
    generation: str | None = None
    desirability_rank: int = 99  # lower is better


class ShortlistUpdate(BaseModel):
    status: str | None = None
    interest_level: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None
    dealer_contacted: bool | None = None
    dealer_response: str | None = None
    offered_price_zar: int | None = None
    counteroffer_zar: int | None = None
    viewing_date: datetime | None = None
    rejection_reason: str | None = None


class ShortlistCreate(BaseModel):
    canonical_vehicle_id: int
    status: str = "watching"
    interest_level: int = Field(default=3, ge=1, le=5)
    notes: str | None = None


class MergeRequest(BaseModel):
    listing_ids: list[int] = Field(min_length=2)
    notes: str | None = None


class UnmergeRequest(BaseModel):
    listing_id: int


class VehicleFilterParams(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_price: int | None = None
    max_price: int | None = None
    min_mileage: int | None = None
    max_mileage: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    variant: str | None = None
    province: str | None = None
    dealer: str | None = None
    drivetrain: str | None = None
    min_deal_score: float | None = None
    min_days: int | None = None
    max_days: int | None = None
    has_reduction: bool | None = None
    new_only: bool | None = None
    shortlisted: bool | None = None
    stretch: bool | None = None
    active_only: bool = True
    sort: str = "deal_score_desc"
    q: str | None = None


class DashboardStats(BaseModel):
    active_matching: int
    new_today: int
    reductions_this_week: int
    median_asking_price: int | None
    top_deal: dict[str, Any] | None
    best_grs: dict[str, Any] | None
    best_vx: dict[str, Any] | None
    most_motivated: dict[str, Any] | None
    disclaimer: str = (
        "Prices shown are asking prices from public listings, not confirmed selling prices."
    )


class MessageDraftRequest(BaseModel):
    preferred_offer_zar: int | None = None
    include_finance: bool = False
    include_trade_in: bool = False
    tone: str = "polite"
