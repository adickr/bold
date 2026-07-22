"""Query helpers for vehicles and dashboard stats."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.entities import CanonicalVehicle, PriceEvent
from app.schemas.listings import DashboardStats, VehicleFilterParams
from app.services.media import absolute_url, source_label

# Towns/areas commonly listed without "Western Cape" in the location string.
_WESTERN_CAPE_HINTS = (
    "western cape",
    "cape town",
    "stellenbosch",
    "paarl",
    "somerset west",
    "brackenfell",
    "bellville",
    "george",
    "knysna",
    "mossel bay",
    "worcester",
    "strand",
    "gordons bay",
    "gordon's bay",
    "hermanus",
    "malmesbury",
    "durbanville",
    "kuilsriver",
    "kuils river",
    "milnerton",
    "table view",
    "century city",
    "observatory",
    "claremont",
    "wynberg",
    "constantia",
    "fish hoek",
    "simonstown",
    "simon's town",
    "hout bay",
    "atlantis",
    "velddrif",
    "piketberg",
    "caledon",
    "swellendam",
    "beaufort west",
    "oudtshoorn",
    "plettenberg",
    "goodwood",
    "parow",
    "tyger valley",
    "tygervalley",
    "century city",
    "woodstock",
    "observatory",
    "ottery",
    "foreshores",
    "foreshore",
    "green point",
    "sea point",
    "rondebosch",
    "newlands",
    "plumstead",
    "diep river",
    "tokai",
    "muizenberg",
    "strandfontein",
    "philippi",
    "mitchells plain",
    "mitchell's plain",
    "khayelitsha",
    "somerset west",
    "strand",
    "n1 city",
    "montague gardens",
    "killarney",
    "melkbos",
    "blouberg",
    "parklands",
    "west coast",
)


def default_buyer_filters(**overrides: Any) -> VehicleFilterParams:
    """Defaults for browsing: ≤100k km, 4x4, Western Cape, cheapest first."""
    settings = get_settings()
    data: dict[str, Any] = {
        "max_mileage": settings.max_mileage_km,
        "drivetrain": settings.required_drivetrain,
        "province": settings.preferred_province,
        "sort": settings.default_sort,
        "active_only": True,
    }
    for key, value in overrides.items():
        if value is not None:
            data[key] = value
    return VehicleFilterParams(**data)


def location_matches_province(location: str | None, province: str | None) -> bool:
    if not location or not province:
        return False
    loc = location.lower()
    prov = province.strip().lower()
    if prov in loc:
        return True
    if prov in {"western cape", "wc", "western-cape"}:
        return any(hint in loc for hint in _WESTERN_CAPE_HINTS)
    return False


def _drivetrain_matches(value: str | None, wanted: str) -> bool:
    """Match drivetrain. For 4x4 preference, keep unclear listings (exclude only 4x2)."""
    right = wanted.lower().replace(" ", "")
    left = (value or "").lower().replace(" ", "")
    if right in {"4x4", "4wd", "awd"}:
        if not left:
            return True
        if left in {"4x2", "2wd"} or "4x2" in left:
            return False
        return left in {"4x4", "4wd", "awd"} or "4x4" in left or "4wd" in left
    if right in {"4x2", "2wd"}:
        if not left:
            return False
        return left in {"4x2", "2wd"} or "4x2" in left
    if not left:
        return False
    return right in left


def sort_vehicles(vehicles: list[CanonicalVehicle], sort: str) -> list[CanonicalVehicle]:
    key = (sort or "price_asc").strip().lower()
    # Unknown mileage is kept in results but demoted below known-mileage cars
    unknown_mileage = lambda v: v.current_mileage_km is None
    if key == "price_desc":
        return sorted(
            vehicles,
            key=lambda v: (
                v.current_lowest_price is None,
                unknown_mileage(v),
                -(v.current_lowest_price or 0),
            ),
        )
    if key == "mileage_asc":
        return sorted(
            vehicles,
            key=lambda v: (
                v.current_mileage_km is None,
                v.current_mileage_km or 0,
            ),
        )
    if key == "deal_score_desc":
        return sorted(
            vehicles,
            key=lambda v: (
                v.deal_score is None,
                unknown_mileage(v),
                -(v.deal_score or 0),
            ),
        )
    if key == "year_desc":
        return sorted(
            vehicles,
            key=lambda v: (
                v.year is None,
                unknown_mileage(v),
                -(v.year or 0),
            ),
        )
    # Default / price_asc: known mileage first, then lowest asking price
    return sorted(
        vehicles,
        key=lambda v: (
            v.current_lowest_price is None,
            unknown_mileage(v),
            v.current_lowest_price or 0,
        ),
    )


def vehicle_to_dict(v: CanonicalVehicle) -> dict[str, Any]:
    shortlist = v.shortlist_entry
    sources = []
    for s in getattr(v, "source_listings", []) or []:
        sources.append(
            {
                "id": s.id,
                "source": s.source,
                "label": source_label(s.source),
                "url": absolute_url(s.url, source=s.source) or s.url,
                "price": s.price_zar,
                "status": s.listing_status,
                "title": s.title,
            }
        )
    # Prefer cheapest *active* source as primary outbound link
    active_sources = [
        s
        for s in sources
        if s.get("status") in {"active", "relisted"}
    ]
    primary_source = None
    if active_sources:
        primary_source = sorted(
            active_sources,
            key=lambda x: x.get("price") if x.get("price") is not None else 10**12,
        )[0]
    elif sources:
        # Fall back only for detail context; UI should still mark removed
        primary_source = sources[0]

    image_candidates: list[str] = []
    if v.primary_image_url:
        abs_primary = absolute_url(v.primary_image_url)
        if abs_primary:
            image_candidates.append(abs_primary)
    for s in getattr(v, "source_listings", []) or []:
        if s.listing_status not in {"active", "relisted"}:
            continue
        for img in s.image_urls or []:
            abs_img = absolute_url(img, source=s.source)
            if abs_img and abs_img not in image_candidates:
                image_candidates.append(abs_img)
    # If only removed sources have images, still show something
    if not image_candidates:
        for s in getattr(v, "source_listings", []) or []:
            for img in s.image_urls or []:
                abs_img = absolute_url(img, source=s.source)
                if abs_img and abs_img not in image_candidates:
                    image_candidates.append(abs_img)

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
        "image": image_candidates[0] if image_candidates else None,
        "images": image_candidates[:8],
        "sources": sources,
        "primary_source": primary_source,
        "detail_path": f"/vehicles/{v.id}",
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
        # Keep unknown mileage — search pages often omit it
        stmt = stmt.where(
            or_(
                CanonicalVehicle.current_mileage_km.is_(None),
                CanonicalVehicle.current_mileage_km <= params.max_mileage,
            )
        )
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

    vehicles = list(db.execute(stmt).scalars().all())

    if params.province:
        vehicles = [
            v for v in vehicles if location_matches_province(v.primary_location, params.province)
        ]
    if params.drivetrain:
        vehicles = [
            v for v in vehicles if _drivetrain_matches(v.drivetrain, params.drivetrain)
        ]

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
    return sort_vehicles(vehicles, params.sort)


def dashboard_stats(db: Session) -> DashboardStats:
    """Spotlights can surface exceptional cars nationwide; counts use buyer defaults."""
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
    matching = filter_vehicles(db, default_buyer_filters())
    prices = [v.current_lowest_price for v in matching if v.current_lowest_price]
    new_today = sum(
        1
        for v in matching
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
        # Prefer buyer-criteria matches; fall back to any active for "awesome" finds
        matched = [v for v in matching if pred(v) and v.deal_score is not None]
        if not matched:
            matched = [v for v in matching if pred(v)]
        if not matched:
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
    pool = matching or list(actives)
    if pool:
        ranked = sorted(
            [v for v in pool if v.motivation_score is not None],
            key=lambda v: v.motivation_score or 0,
            reverse=True,
        )
        if ranked:
            motivated = vehicle_to_dict(ranked[0])

    return DashboardStats(
        active_matching=len(matching),
        new_today=new_today,
        reductions_this_week=len(reductions),
        median_asking_price=int(median(prices)) if prices else None,
        top_deal=top_deal,
        best_grs=best_grs,
        best_vx=best_vx,
        most_motivated=motivated,
    )
