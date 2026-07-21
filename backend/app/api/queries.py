"""Query helpers for vehicles and dashboard stats."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import CanonicalVehicle, PriceEvent, ShortlistEntry
from app.schemas.listings import DashboardStats, VehicleFilterParams


def vehicle_to_dict(v: CanonicalVehicle) -> dict[str, Any]:
    shortlist = v.shortlist_entry
    return {
        "id": v.id,
        "year": v.year,
        "variant": v.variant_normalised,
        "trim": v.trim,
        "price": v.current_lowest_price,
        "mileage": v.current_mileage_km,
        "location": v.primary_location,
        "dealer": v.primary_dealer,
        "days_tracked": v.days_tracked,
        "total_reduction": v.total_reduction_zar,
        "deal_score": v.deal_score,
        "motivation_score": v.motivation_score,
        "motivation_level": v.motivation_level,
        "source_count": v.source_count,
        "shortlist_status": shortlist.status if shortlist else None,
        "is_stretch": v.is_stretch_candidate,
        "image": v.primary_image_url,
        "risk_flags": v.risk_flags or [],
        "is_active": v.is_active,
        "drivetrain": v.drivetrain,
        "colour": v.colour,
        "engine": v.engine,
        "confirmed": ["price", "mileage", "source_links"],
        "inferred": ["deal_score", "motivation_score", "comparable_stats"],
    }


def filter_vehicles(db: Session, params: VehicleFilterParams) -> list[CanonicalVehicle]:
    stmt = select(CanonicalVehicle).options(
        selectinload(CanonicalVehicle.shortlist_entry),
        selectinload(CanonicalVehicle.source_listings),
        selectinload(CanonicalVehicle.price_events),
    )
    if params.active_only:
        stmt = stmt.where(CanonicalVehicle.is_active.is_(True))
    if params.min_price is not None:
        stmt = stmt.where(CanonicalVehicle.current_lowest_price >= params.min_price)
    if params.max_price is not None:
        stmt = stmt.where(CanonicalVehicle.current_lowest_price <= params.max_price)
    if params.min_mileage is not None:
        stmt = stmt.where(CanonicalVehicle.current_mileage_km >= params.min_mileage)
    if params.max_mileage is not None:
        stmt = stmt.where(CanonicalVehicle.current_mileage_km <= params.max_mileage)
    if params.min_year is not None:
        stmt = stmt.where(CanonicalVehicle.year >= params.min_year)
    if params.max_year is not None:
        stmt = stmt.where(CanonicalVehicle.year <= params.max_year)
    if params.min_deal_score is not None:
        stmt = stmt.where(CanonicalVehicle.deal_score >= params.min_deal_score)
    if params.min_days is not None:
        stmt = stmt.where(CanonicalVehicle.days_tracked >= params.min_days)
    if params.max_days is not None:
        stmt = stmt.where(CanonicalVehicle.days_tracked <= params.max_days)
    if params.stretch is True:
        stmt = stmt.where(CanonicalVehicle.is_stretch_candidate.is_(True))
    if params.has_reduction is True:
        stmt = stmt.where(CanonicalVehicle.total_reduction_zar > 0)
    if params.variant:
        stmt = stmt.where(CanonicalVehicle.variant_normalised.ilike(f"%{params.variant}%"))
    if params.dealer:
        stmt = stmt.where(CanonicalVehicle.primary_dealer.ilike(f"%{params.dealer}%"))
    if params.province:
        stmt = stmt.where(CanonicalVehicle.primary_location.ilike(f"%{params.province}%"))

    vehicles = list(db.execute(stmt.order_by(CanonicalVehicle.deal_score.desc().nullslast())).scalars().all())

    if params.shortlisted:
        vehicles = [v for v in vehicles if v.shortlist_entry is not None]
    if params.new_only:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        vehicles = [
            v
            for v in vehicles
            if v.first_seen_at
            and (
                v.first_seen_at
                if v.first_seen_at.tzinfo
                else v.first_seen_at.replace(tzinfo=timezone.utc)
            )
            >= since
        ]
    if params.q:
        q = params.q.lower()
        vehicles = [
            v
            for v in vehicles
            if q in (v.variant_normalised or "").lower()
            or q in (v.primary_dealer or "").lower()
            or q in (v.primary_location or "").lower()
            or q in (v.trim or "").lower()
        ]
    return vehicles


def dashboard_stats(db: Session) -> DashboardStats:
    now = datetime.now(timezone.utc)
    today = now - timedelta(hours=24)
    week = now - timedelta(days=7)
    actives = (
        db.execute(
            select(CanonicalVehicle)
            .where(CanonicalVehicle.is_active.is_(True))
            .options(selectinload(CanonicalVehicle.shortlist_entry))
        )
        .scalars()
        .all()
    )
    prices = [v.current_lowest_price for v in actives if v.current_lowest_price]
    new_today = sum(
        1
        for v in actives
        if v.first_seen_at
        and (
            v.first_seen_at
            if v.first_seen_at.tzinfo
            else v.first_seen_at.replace(tzinfo=timezone.utc)
        )
        >= today
    )
    reductions = (
        db.execute(
            select(PriceEvent).where(PriceEvent.observed_at >= week, PriceEvent.change_zar < 0)
        )
        .scalars()
        .all()
    )

    def pick(pred):
        matched = [v for v in actives if pred(v) and v.deal_score is not None]
        if not matched:
            matched = [v for v in actives if pred(v)]
        if not matched:
            return None
        best = max(matched, key=lambda v: v.deal_score or 0)
        return vehicle_to_dict(best)

    top_deal = pick(lambda v: True)
    best_grs = pick(lambda v: v.trim == "GR-S" or (v.special_edition or "").startswith("GR"))
    best_vx = pick(lambda v: v.trim == "VX")
    motivated = None
    if actives:
        ranked = sorted(
            [v for v in actives if v.motivation_score is not None],
            key=lambda v: v.motivation_score or 0,
            reverse=True,
        )
        if ranked:
            motivated = vehicle_to_dict(ranked[0])

    return DashboardStats(
        active_matching=len(actives),
        new_today=new_today,
        reductions_this_week=len(reductions),
        median_asking_price=int(median(prices)) if prices else None,
        top_deal=top_deal,
        best_grs=best_grs,
        best_vx=best_vx,
        most_motivated=motivated,
    )
