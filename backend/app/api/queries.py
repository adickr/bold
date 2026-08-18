"""Query helpers for vehicles and dashboard stats."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.entities import CanonicalVehicle, CollectorRun, ListingObservation, PriceEvent, SourceListing, ShortlistEntry, ShortlistStatus
from app.schemas.listings import DashboardStats, VehicleFilterParams
from app.services.media import absolute_url, is_valid_marketplace_url, normalise_listing_url, source_label
from app.services.market_snapshot import build_market_history
from app.services.normalise import colour_swatch_css
from app.services.scoring import with_score_inputs
from app.services.stock import model_shot

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


def default_buyer_filters(db: Session | None = None, **overrides: Any) -> VehicleFilterParams:
    """Defaults for browsing — aligned with the active search profile when available."""
    try:
        from app.services.search_profile import active_buyer_filters

        return active_buyer_filters(db=db, **overrides)
    except Exception:
        settings = get_settings()
        data: dict[str, Any] = {
            "max_mileage": settings.max_mileage_km,
            "drivetrain": settings.required_drivetrain or None,
            "province": settings.preferred_province or None,
            "make": settings.make or None,
            "model": settings.model or None,
            "fuel_type": settings.required_fuel or None,
            "sort": settings.default_sort,
            "active_only": True,
        }
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
        return VehicleFilterParams(**data)


def nationwide_hunt_filters(db: Session | None = None) -> VehicleFilterParams:
    """All provinces for the active hunt — never mix Fortuner with RAV4."""
    hunt = default_buyer_filters(db)
    return VehicleFilterParams(
        active_only=True,
        sort="deal_score_desc",
        make=hunt.make,
        model=hunt.model,
        fuel_type=hunt.fuel_type,
    )


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
    """Match drivetrain. 4x4 filter requires an explicit 4x4/4wd label."""
    right = wanted.lower().replace(" ", "")
    left = (value or "").lower().replace(" ", "")
    if right in {"4x4", "4wd", "awd"}:
        if not left:
            return False
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


def vote_from_shortlist(entry: ShortlistEntry | None) -> str | None:
    if entry is None:
        return None
    if entry.status == ShortlistStatus.REJECTED.value:
        return "down"
    return "up"


def sort_vehicles(vehicles: list[CanonicalVehicle], sort: str) -> list[CanonicalVehicle]:
    key = (sort or "deal_score_desc").strip().lower()
    # Unknown mileage is kept in results but demoted below known-mileage cars
    unknown_mileage = lambda v: v.current_mileage_km is None
    # Thumbs-down always sink to the bottom of any sort
    thumbs_down = lambda v: (
        v.shortlist_entry is not None
        and v.shortlist_entry.status == ShortlistStatus.REJECTED.value
    )
    if key == "price_desc":
        return sorted(
            vehicles,
            key=lambda v: (
                thumbs_down(v),
                v.current_lowest_price is None,
                unknown_mileage(v),
                -(v.current_lowest_price or 0),
            ),
        )
    if key == "mileage_asc":
        return sorted(
            vehicles,
            key=lambda v: (
                thumbs_down(v),
                v.current_mileage_km is None,
                v.current_mileage_km or 0,
            ),
        )
    if key == "price_asc":
        return sorted(
            vehicles,
            key=lambda v: (
                thumbs_down(v),
                v.current_lowest_price is None,
                unknown_mileage(v),
                v.current_lowest_price or 0,
            ),
        )
    if key == "year_desc":
        return sorted(
            vehicles,
            key=lambda v: (
                thumbs_down(v),
                v.year is None,
                unknown_mileage(v),
                -(v.year or 0),
            ),
        )
    # Default / deal_score_desc: best deals first; unknown scores last
    return sorted(
        vehicles,
        key=lambda v: (
            thumbs_down(v),
            v.deal_score is None,
            unknown_mileage(v),
            -(v.deal_score or 0),
            v.current_lowest_price is None,
            v.current_lowest_price or 0,
        ),
    )


def vehicle_to_dict(v: CanonicalVehicle) -> dict[str, Any]:
    shortlist = v.shortlist_entry
    sources = []
    for s in getattr(v, "source_listings", []) or []:
        url = normalise_listing_url(
            s.source,
            s.url,
            listing_id=s.source_listing_id,
            title=s.title,
            variant=s.variant_raw or s.variant_normalised,
            year=s.year,
        )
        if not url or not is_valid_marketplace_url(s.source, url):
            # Still show agent-side status but no broken outbound button
            url = None
        sources.append(
            {
                "id": s.id,
                "source": s.source,
                "label": source_label(s.source),
                "url": url,
                "price": s.price_zar,
                "status": s.listing_status,
                "title": s.title,
            }
        )
    # Prefer cheapest *active* source with a working URL as primary outbound link
    active_sources = [
        s
        for s in sources
        if s.get("status") in {"active", "relisted"} and s.get("url")
    ]
    primary_source = None
    if active_sources:
        primary_source = sorted(
            active_sources,
            key=lambda x: x.get("price") if x.get("price") is not None else 10**12,
        )[0]
    elif sources:
        # Fall back only for detail context; UI should still mark removed
        with_url = [s for s in sources if s.get("url")]
        primary_source = with_url[0] if with_url else sources[0]

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
        "make": v.make,
        "model": v.model,
        "variant": v.variant_normalised,
        "trim": v.trim,
        "price": v.current_lowest_price,
        "mileage": v.current_mileage_km,
        "location": v.primary_location,
        "dealer": v.primary_dealer,
        "days_tracked": v.days_tracked,
        "total_reduction": v.total_reduction_zar,
        "deal_score": v.deal_score,
        "deal_score_breakdown": with_score_inputs(v, v.deal_score_breakdown, kind="deal"),
        "motivation_score": v.motivation_score,
        "motivation_level": v.motivation_level,
        "motivation_breakdown": with_score_inputs(
            v, v.motivation_breakdown, kind="motivation"
        ),
        "source_count": v.source_count,
        "shortlist_status": shortlist.status if shortlist else None,
        "vote": vote_from_shortlist(shortlist),
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
        "colour_css": colour_swatch_css(v.colour),
        "model_shot": model_shot(v),
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
    if params.make:
        wanted_make = params.make.lower()
        vehicles = [v for v in vehicles if (v.make or "").lower() == wanted_make]
    if params.model:
        from app.services.hunt import looks_like_model

        vehicles = [
            v
            for v in vehicles
            if looks_like_model(v.model, v.variant_normalised, v.trim, model=params.model)
        ]
    if params.fuel_type:
        from app.services.hunt import listing_matches_fuel

        vehicles = [
            v
            for v in vehicles
            if listing_matches_fuel(
                v.fuel_type,
                v.engine,
                v.variant_normalised,
                v.trim,
                required_fuel=params.fuel_type,
            )
        ]

    if params.shortlisted:
        vehicles = [
            v for v in vehicles if vote_from_shortlist(v.shortlist_entry) == "up"
        ]
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


def _aware_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def build_last_fetch_summary(db: Session) -> dict[str, Any] | None:
    """Summarise the most recent collect wave: when it ran and what changed."""
    runs = list(
        db.execute(
            select(CollectorRun)
            .where(CollectorRun.finished_at.is_not(None))
            .order_by(CollectorRun.finished_at.desc())
            .limit(40)
        )
        .scalars()
        .all()
    )
    if not runs:
        return None

    last_at = _aware_dt(runs[0].finished_at)
    if last_at is None:
        return None

    wave: list[CollectorRun] = []
    seen_sources: set[str] = set()
    for run in runs:
        finished = _aware_dt(run.finished_at)
        if finished is None:
            continue
        if (last_at - finished).total_seconds() > 20 * 60:
            break
        if run.source in seen_sources:
            continue
        seen_sources.add(run.source)
        wave.append(run)

    session_start = min((_aware_dt(r.started_at) or last_at) for r in wave)
    new_count = sum(int(r.listings_new or 0) for r in wave)
    updated_count = sum(int(r.listings_updated or 0) for r in wave)
    found_count = sum(int(r.listings_found or 0) for r in wave)
    sources_ok = sum(1 for r in wave if r.success)
    sources_total = len(wave)

    price_cuts = int(
        db.execute(
            select(func.count(PriceEvent.id)).where(
                PriceEvent.observed_at >= session_start,
                PriceEvent.change_zar < 0,
            )
        ).scalar_one()
        or 0
    )

    new_items, cut_items, update_items, gone_items = _fetch_change_items(db, session_start)
    gone_recent = _fetch_gone_items(db, since=last_at - timedelta(days=7), until=None, limit=24)
    gone_ids = {g["id"] for g in gone_items if g.get("id") is not None}
    # Gone deck: session disappearances first, then fill from the last 7 days
    gone_deck: list[dict[str, Any]] = list(gone_items)
    for item in gone_recent:
        if item.get("id") in gone_ids:
            continue
        gone_deck.append(item)
        if len(gone_deck) >= 24:
            break

    # Box strip: material changes (new / cuts / gone)
    highlights: list[dict[str, Any]] = []
    for item in new_items[:5]:
        score = item.get("deal_score")
        score_bit = f"score {score}" if score is not None else None
        detail_bits = [b for b in (score_bit, item.get("price_label")) if b]
        highlights.append(
            {
                "kind": "new",
                "label": f"New · {item['title']}",
                "detail": " · ".join(detail_bits) if detail_bits else None,
                "href": item["href"],
            }
        )
    for item in cut_items:
        if len(highlights) >= 8:
            break
        highlights.append(
            {
                "kind": "cut",
                "label": f"Price cut · {item['title']}",
                "detail": item.get("change_label"),
                "href": item["href"],
            }
        )
    for item in gone_items:
        if len(highlights) >= 10:
            break
        days_bit = f"{item['days_listed']}d listed" if item.get("days_listed") is not None else None
        highlights.append(
            {
                "kind": "gone",
                "label": f"Gone · {item['title']}",
                "detail": " · ".join(
                    b for b in (item.get("price_label"), days_bit) if b
                )
                or None,
                "href": item["href"],
            }
        )

    gone_count = len(gone_items)
    has_material = bool(new_items or cut_items or gone_items or new_count or price_cuts)
    has_updates = bool(update_items or updated_count)
    has_changes = bool(has_material or has_updates or gone_deck)
    return {
        "last_fetch_at": last_at.isoformat(),
        "session_started_at": session_start.isoformat(),
        "new": new_count,
        "updated": updated_count,
        "found": found_count,
        "price_cuts": price_cuts,
        "gone": gone_count,
        "sources_ok": sources_ok,
        "sources_total": sources_total,
        "has_changes": has_changes,
        "has_material": has_material,
        "has_updates": has_updates,
        "highlights": highlights,
        "changes": {
            "new": new_items,
            "price_cuts": cut_items,
            "updates": update_items,
            "gone": gone_items,
            "gone_recent": gone_deck,
        },
        "summary": _fetch_material_summary(new_count, price_cuts, gone_count),
        "full_summary": _fetch_change_summary(
            new_count, updated_count, price_cuts, gone_count, has_changes
        ),
        "updates_label": _updates_link_label(
            len(new_items) + len(cut_items) + gone_count + len(update_items),
            new_count + updated_count + price_cuts + gone_count,
        ),
    }


def _fmt_zar_spaces(value: int | None) -> str | None:
    if value is None:
        return None
    return f"R{int(value):,}".replace(",", " ")


def _fmt_km_spaces(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{int(value):,}".replace(",", " ") + " km"


def _vehicle_change_title(vehicle: CanonicalVehicle) -> str:
    return f"{vehicle.year or ''} {vehicle.variant_normalised or vehicle.model or 'listing'}".strip()


def _observation_field_details(
    obs: ListingObservation, prev: ListingObservation | None
) -> tuple[list[str], list[str], int | None]:
    """Human-readable field changes, raw field keys, and price delta if any."""
    raw_fields = [f for f in (obs.changed_fields or []) if f and f != "created"]
    if not raw_fields:
        return [], [], None

    details: list[str] = []
    change_zar: int | None = None
    for field in raw_fields:
        if field == "relisted":
            details.append("Relisted")
        elif field == "price_zar":
            old = prev.price_zar if prev else None
            new = obs.price_zar
            if old is not None and new is not None:
                change_zar = int(new) - int(old)
                arrow = f"{_fmt_zar_spaces(old)} → {_fmt_zar_spaces(new)}"
                if new < old:
                    details.append(f"Price cut {arrow}")
                elif new > old:
                    details.append(f"Price up {arrow}")
                else:
                    details.append("Price unchanged")
            elif new is not None:
                details.append(f"Price {_fmt_zar_spaces(new)}")
            else:
                details.append("Price updated")
        elif field == "mileage_km":
            old = prev.mileage_km if prev else None
            new = obs.mileage_km
            if old is not None and new is not None and old != new:
                details.append(f"Mileage {_fmt_km_spaces(old)} → {_fmt_km_spaces(new)}")
            elif new is not None:
                details.append(f"Mileage {_fmt_km_spaces(new)}")
            else:
                details.append("Mileage updated")
        elif field == "dealer_name":
            old = (prev.dealer_name if prev else None) or None
            new = obs.dealer_name
            if old and new and old != new:
                details.append(f"Dealer {old} → {new}")
            elif new:
                details.append(f"Dealer {new}")
            else:
                details.append("Dealer updated")
        elif field == "title":
            details.append("Title updated")
        elif field == "description":
            details.append("Description updated")
        else:
            details.append(field.replace("_", " ").capitalize())
    return details, raw_fields, change_zar


def _days_between(start: datetime | None, end: datetime | None) -> int | None:
    a, b = _aware_dt(start), _aware_dt(end)
    if a is None or b is None:
        return None
    return max(0, (b - a).days)


def _fetch_gone_items(
    db: Session,
    *,
    since: datetime,
    until: datetime | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Listings/vehicles that left the market (status=removed), criteria-aware."""
    from app.services.market_snapshot import _soft_match_listing, _soft_match_vehicle
    from app.services.search_profile import get_active_criteria

    criteria = get_active_criteria(db)
    listings = list(
        db.execute(
            select(SourceListing)
            .options(selectinload(SourceListing.canonical_vehicle))
            .where(
                SourceListing.listing_status == "removed",
                SourceListing.updated_at >= since,
            )
            .order_by(SourceListing.updated_at.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    seen_vehicle_ids: set[int] = set()
    until_aware = _aware_dt(until) if until else None
    for listing in listings:
        when = _aware_dt(listing.updated_at)
        if when is None:
            continue
        if until_aware and when >= until_aware:
            continue
        vehicle = listing.canonical_vehicle
        if vehicle is not None:
            if vehicle.id in seen_vehicle_ids:
                continue
            if not _soft_match_vehicle(vehicle, criteria):
                continue
            seen_vehicle_ids.add(vehicle.id)
            title = _vehicle_change_title(vehicle)
            price = vehicle.current_lowest_price or listing.price_zar
            mileage = vehicle.current_mileage_km or listing.mileage_km
            location = vehicle.primary_location or listing.dealer_location
            href = f"/vehicles/{vehicle.id}"
            first = vehicle.first_seen_at or listing.first_seen_at
            vehicle_id = vehicle.id
        else:
            if not _soft_match_listing(listing, criteria):
                continue
            title = (listing.title or "Fortuner").strip()
            price = listing.price_zar
            mileage = listing.mileage_km
            location = listing.dealer_location
            href = f"/listings?q={listing.source_listing_id}"
            first = listing.first_seen_at
            vehicle_id = None
        days_listed = _days_between(first, when)
        items.append(
            {
                "kind": "gone",
                "id": vehicle_id if vehicle_id is not None else -listing.id,
                "title": title,
                "price": price,
                "price_label": _fmt_zar_spaces(price),
                "mileage": mileage,
                "location": location,
                "days_listed": days_listed,
                "gone_at": when.isoformat(),
                "source": listing.source,
                "source_label": source_label(listing.source),
                "href": href,
            }
        )
        if len(items) >= limit:
            break
    return items


def _fetch_change_items(
    db: Session, session_start: datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Concrete new listings, price cuts, field updates, and disappearances."""
    new_vehicles = (
        db.execute(
            select(CanonicalVehicle)
            .where(
                CanonicalVehicle.is_active.is_(True),
                CanonicalVehicle.first_seen_at >= session_start,
            )
            .order_by(CanonicalVehicle.first_seen_at.desc())
            .limit(40)
        )
        .scalars()
        .all()
    )
    new_items: list[dict[str, Any]] = []
    new_ids = {v.id for v in new_vehicles}
    for v in new_vehicles:
        new_items.append(
            {
                "kind": "new",
                "id": v.id,
                "title": _vehicle_change_title(v),
                "price": v.current_lowest_price,
                "price_label": _fmt_zar_spaces(v.current_lowest_price),
                "deal_score": v.deal_score,
                "mileage": v.current_mileage_km,
                "location": v.primary_location,
                "href": f"/vehicles/{v.id}",
            }
        )

    cut_events = list(
        db.execute(
            select(PriceEvent)
            .where(PriceEvent.observed_at >= session_start, PriceEvent.change_zar < 0)
            .order_by(PriceEvent.observed_at.desc())
            .limit(40)
        )
        .scalars()
        .all()
    )
    cut_items: list[dict[str, Any]] = []
    seen_cut_ids: set[int] = set()
    for event in cut_events:
        if not event.canonical_vehicle_id or event.canonical_vehicle_id in seen_cut_ids:
            continue
        vehicle = db.get(CanonicalVehicle, event.canonical_vehicle_id)
        if not vehicle:
            continue
        seen_cut_ids.add(vehicle.id)
        drop = abs(int(event.change_zar or 0))
        cut_items.append(
            {
                "kind": "cut",
                "id": vehicle.id,
                "title": _vehicle_change_title(vehicle),
                "price": event.new_price_zar or vehicle.current_lowest_price,
                "price_label": _fmt_zar_spaces(event.new_price_zar or vehicle.current_lowest_price),
                "old_price": event.old_price_zar,
                "change_zar": event.change_zar,
                "change_label": f"−{_fmt_zar_spaces(drop)}" if drop else None,
                "mileage": vehicle.current_mileage_km,
                "location": vehicle.primary_location,
                "href": f"/vehicles/{vehicle.id}",
            }
        )

    observations = list(
        db.execute(
            select(ListingObservation)
            .where(ListingObservation.observed_at >= session_start)
            .order_by(ListingObservation.observed_at.desc())
            .limit(250)
        )
        .scalars()
        .all()
    )
    update_items: list[dict[str, Any]] = []
    seen_listing_ids: set[int] = set()
    for obs in observations:
        if obs.source_listing_id in seen_listing_ids:
            continue
        fields = [f for f in (obs.changed_fields or []) if f and f != "created"]
        if not fields:
            continue
        seen_listing_ids.add(obs.source_listing_id)

        listing = db.get(SourceListing, obs.source_listing_id)
        if not listing or not listing.canonical_vehicle_id:
            continue
        if listing.canonical_vehicle_id in new_ids:
            continue
        vehicle = db.get(CanonicalVehicle, listing.canonical_vehicle_id)
        if not vehicle:
            continue

        prev = db.execute(
            select(ListingObservation)
            .where(
                ListingObservation.source_listing_id == obs.source_listing_id,
                ListingObservation.id != obs.id,
                ListingObservation.observed_at < obs.observed_at,
            )
            .order_by(ListingObservation.observed_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        details, raw_fields, change_zar = _observation_field_details(obs, prev)
        if not details:
            continue
        update_items.append(
            {
                "kind": "update",
                "id": vehicle.id,
                "listing_id": listing.id,
                "title": _vehicle_change_title(vehicle),
                "price": vehicle.current_lowest_price,
                "price_label": _fmt_zar_spaces(vehicle.current_lowest_price),
                "mileage": vehicle.current_mileage_km,
                "location": vehicle.primary_location or listing.dealer_location,
                "fields": raw_fields,
                "details": details,
                "change_summary": " · ".join(details),
                "change_zar": change_zar,
                "href": f"/vehicles/{vehicle.id}",
                "source": listing.source,
            }
        )
        if len(update_items) >= 40:
            break

    gone_items = _fetch_gone_items(db, since=session_start, limit=40)
    return new_items, cut_items, update_items, gone_items


def _fetch_material_summary(new: int, cuts: int, gone: int = 0) -> str:
    if not new and not cuts and not gone:
        return "No new stock, price cuts, or disappearances"
    parts: list[str] = []
    if new:
        parts.append(f"{new} new")
    if cuts:
        parts.append(f"{cuts} price cut{'s' if cuts != 1 else ''}")
    if gone:
        parts.append(f"{gone} gone")
    return " · ".join(parts)


def _updates_link_label(shown: int, reported: int) -> str:
    count = max(shown, reported)
    if count <= 0:
        return "Browse updates"
    return f"Browse updates ({count})"


def _fetch_change_summary(
    new: int, updated: int, cuts: int, gone: int, has_changes: bool
) -> str:
    if not has_changes:
        return "No listing changes in the last scan"
    parts: list[str] = []
    if new:
        parts.append(f"{new} new")
    if cuts:
        parts.append(f"{cuts} price cut{'s' if cuts != 1 else ''}")
    if gone:
        parts.append(f"{gone} gone")
    if updated:
        parts.append(f"{updated} updated")
    return " · ".join(parts)


def build_listing_duration_stats(db: Session) -> dict[str, Any]:
    """Median days active stock has been tracked + days-to-disappear for recent gone cars."""
    matching = filter_vehicles(db, default_buyer_filters(db))
    active_days = [int(v.days_tracked) for v in matching if v.days_tracked is not None]
    since = datetime.now(timezone.utc) - timedelta(days=30)
    gone = _fetch_gone_items(db, since=since, limit=80)
    gone_days = [int(g["days_listed"]) for g in gone if g.get("days_listed") is not None]
    return {
        "median_days_listed": int(median(active_days)) if active_days else None,
        "active_sample_size": len(active_days),
        "median_days_to_gone": int(median(gone_days)) if gone_days else None,
        "gone_sample_size": len(gone_days),
    }


def build_price_distribution(
    prices: list[int] | None = None,
    *,
    entries: list[tuple[int, bool]] | None = None,
    bucket_zar: int = 50_000,
) -> list[dict[str, Any]]:
    """Histogram buckets for asking prices (inclusive low, exclusive high).

    entries: optional (price, is_new_today) pairs for stacked existing/new bars.
    """
    if entries is None:
        pairs = [(p, False) for p in (prices or []) if p is not None and p > 0]
    else:
        pairs = [(p, bool(is_new)) for p, is_new in entries if p is not None and p > 0]
    if not pairs:
        return []
    clean_prices = sorted(p for p, _ in pairs)
    size = max(10_000, int(bucket_zar))
    lo = (clean_prices[0] // size) * size
    hi = ((clean_prices[-1] // size) + 1) * size
    buckets: list[dict[str, Any]] = []
    for start in range(lo, hi, size):
        end = start + size
        in_bucket = [(p, is_new) for p, is_new in pairs if start <= p < end]
        new_count = sum(1 for _, is_new in in_bucket if is_new)
        count = len(in_bucket)
        prior_count = count - new_count
        buckets.append(
            {
                "min_price": start,
                "max_price": end,
                "count": count,
                "new_count": new_count,
                "prior_count": prior_count,
                "label": _price_bucket_label(start, end),
            }
        )
    while buckets and buckets[0]["count"] == 0:
        buckets.pop(0)
    while buckets and buckets[-1]["count"] == 0:
        buckets.pop()
    return buckets


def _price_bucket_label(start: int, end: int) -> str:
    def fmt(n: int) -> str:
        if n >= 1_000_000:
            val = n / 1_000_000
            return f"R{val:.1f}m".replace(".0m", "m")
        return f"R{n // 1000}k"

    return f"{fmt(start)}–{fmt(end)}"


def dashboard_stats(db: Session) -> DashboardStats:
    """Spotlights can surface exceptional cars nationwide; counts use buyer defaults."""
    now = datetime.now(timezone.utc)
    today = now - timedelta(hours=24)
    week = now - timedelta(days=7)
    actives = (
        db.execute(
            select(CanonicalVehicle)
            .where(CanonicalVehicle.is_active.is_(True))
            .options(
                selectinload(CanonicalVehicle.shortlist_entry),
                selectinload(CanonicalVehicle.source_listings),
            )
        )
        .scalars()
        .all()
    )
    matching = filter_vehicles(db, default_buyer_filters(db))
    prices = [v.current_lowest_price for v in matching if v.current_lowest_price]
    price_entries: list[tuple[int, bool]] = []
    new_today = 0
    for v in matching:
        seen = v.first_seen_at
        if seen and seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        is_new = bool(seen and seen >= today)
        if is_new:
            new_today += 1
        if v.current_lowest_price:
            price_entries.append((v.current_lowest_price, is_new))
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

    median_price = int(median(prices)) if prices else None
    duration = build_listing_duration_stats(db)
    return DashboardStats(
        active_matching=len(matching),
        new_today=new_today,
        reductions_this_week=len(reductions),
        median_asking_price=median_price,
        median_days_listed=duration.get("median_days_listed"),
        median_days_to_gone=duration.get("median_days_to_gone"),
        gone_sample_size=int(duration.get("gone_sample_size") or 0),
        price_distribution=build_price_distribution(entries=price_entries),
        last_fetch=build_last_fetch_summary(db),
        market_history=build_market_history(
            db,
            days=30,
            live_active=len(matching),
            live_median=median_price,
        ),
        top_deal=top_deal,
        best_grs=best_grs,
        best_vx=best_vx,
        most_motivated=motivated,
    )
