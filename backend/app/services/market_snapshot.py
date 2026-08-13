"""Daily market snapshots and stock-flow history for the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import ListingStatus, MarketSnapshot, PriceEvent, SourceListing


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_start(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _soft_match_vehicle(vehicle, criteria: dict[str, Any]) -> bool:
    """Province / axle / mileage / optional hard price — ignore active flag."""
    from app.api.queries import _drivetrain_matches, location_matches_province

    province = criteria.get("province")
    if province and not location_matches_province(vehicle.primary_location, province):
        return False
    drivetrain = criteria.get("required_drivetrain")
    if drivetrain and not _drivetrain_matches(vehicle.drivetrain, drivetrain):
        return False
    max_km = criteria.get("max_mileage_km")
    if max_km is not None and vehicle.current_mileage_km is not None:
        if int(vehicle.current_mileage_km) > int(max_km):
            return False
    if criteria.get("enforce_max_price") and criteria.get("max_price_zar"):
        price = vehicle.current_lowest_price
        if price is not None and int(price) > int(criteria["max_price_zar"]):
            return False
    return True


def _soft_match_listing(listing: SourceListing, criteria: dict[str, Any]) -> bool:
    from app.api.queries import _drivetrain_matches, location_matches_province

    province = criteria.get("province")
    if province and not location_matches_province(listing.dealer_location, province):
        return False
    drivetrain = criteria.get("required_drivetrain")
    if drivetrain and not _drivetrain_matches(listing.drivetrain, drivetrain):
        return False
    max_km = criteria.get("max_mileage_km")
    if max_km is not None and listing.mileage_km is not None:
        if int(listing.mileage_km) > int(max_km):
            return False
    if criteria.get("enforce_max_price") and criteria.get("max_price_zar"):
        if listing.price_zar is not None and int(listing.price_zar) > int(criteria["max_price_zar"]):
            return False
    return True


def _calendar_day_counts(
    db: Session,
    *,
    criteria: dict[str, Any],
    since: datetime,
    until: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    """Bucket new vehicles and removed listings by UTC calendar day."""
    from app.models.entities import CanonicalVehicle

    new_by_day: dict[str, int] = {}
    vehicles = list(
        db.execute(
            select(CanonicalVehicle).where(CanonicalVehicle.first_seen_at >= since)
        )
        .scalars()
        .all()
    )
    for vehicle in vehicles:
        seen = _aware(vehicle.first_seen_at)
        if seen is None or seen >= until:
            continue
        if not _soft_match_vehicle(vehicle, criteria):
            continue
        key = _day_start(seen).date().isoformat()
        new_by_day[key] = new_by_day.get(key, 0) + 1

    removed_by_day: dict[str, int] = {}
    listings = list(
        db.execute(
            select(SourceListing)
            .options(selectinload(SourceListing.canonical_vehicle))
            .where(
                SourceListing.listing_status == ListingStatus.REMOVED.value,
                SourceListing.updated_at >= since,
            )
        )
        .scalars()
        .all()
    )
    # Count one removal per canonical vehicle per day (multi-source ads → one car gone)
    seen_vehicle_days: set[tuple[str, int | None]] = set()
    for listing in listings:
        when = _aware(listing.updated_at)
        if when is None or when >= until:
            continue
        vehicle = listing.canonical_vehicle
        if vehicle is not None:
            if not _soft_match_vehicle(vehicle, criteria):
                continue
            vehicle_key: int | None = vehicle.id
        else:
            if not _soft_match_listing(listing, criteria):
                continue
            vehicle_key = None
        key = _day_start(when).date().isoformat()
        dedupe = (key, vehicle_key if vehicle_key is not None else -listing.id)
        if dedupe in seen_vehicle_days:
            continue
        seen_vehicle_days.add(dedupe)
        removed_by_day[key] = removed_by_day.get(key, 0) + 1

    return new_by_day, removed_by_day


def record_market_snapshot(db: Session) -> MarketSnapshot:
    """Upsert today's snapshot for the active search profile's matching stock."""
    from app.api.queries import default_buyer_filters, filter_vehicles
    from app.services.search_profile import criteria_hash, criteria_label, get_active_criteria

    criteria = get_active_criteria(db)
    c_hash = criteria_hash(criteria)
    matching = filter_vehicles(db, default_buyer_filters(db))
    prices = [v.current_lowest_price for v in matching if v.current_lowest_price]
    mileages = [v.current_mileage_km for v in matching if v.current_mileage_km is not None]
    days = [v.days_tracked for v in matching if v.days_tracked is not None]
    today = _day_start(_utcnow())
    tomorrow = today + timedelta(days=1)
    week_ago = _utcnow() - timedelta(days=7)
    reduction_count = int(
        db.execute(
            select(func.count(PriceEvent.id)).where(
                PriceEvent.observed_at >= week_ago, PriceEvent.change_zar < 0
            )
        ).scalar_one()
        or 0
    )
    breakdown: dict[str, int] = {"GR-S": 0, "VX": 0, "other": 0}
    for v in matching:
        if v.trim == "GR-S" or (v.special_edition or "").startswith("GR"):
            breakdown["GR-S"] += 1
        elif v.trim == "VX":
            breakdown["VX"] += 1
        else:
            breakdown["other"] += 1

    new_by_day, removed_by_day = _calendar_day_counts(
        db, criteria=criteria, since=today, until=tomorrow
    )
    today_key = today.date().isoformat()

    existing = (
        db.execute(
            select(MarketSnapshot)
            .where(
                MarketSnapshot.snapshot_date >= today,
                MarketSnapshot.criteria_hash == c_hash,
            )
            .order_by(MarketSnapshot.snapshot_date.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if existing is None:
        existing = (
            db.execute(
                select(MarketSnapshot)
                .where(
                    MarketSnapshot.snapshot_date >= today,
                    MarketSnapshot.criteria_hash.is_(None),
                )
                .order_by(MarketSnapshot.snapshot_date.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

    payload = dict(
        active_count=len(matching),
        new_count=int(new_by_day.get(today_key, 0)),
        removed_count=int(removed_by_day.get(today_key, 0)),
        reduction_count=reduction_count,
        median_price=int(median(prices)) if prices else None,
        mean_price=float(mean(prices)) if prices else None,
        median_mileage=int(median(mileages)) if mileages else None,
        median_days_on_market=float(median(days)) if days else None,
        variant_breakdown=breakdown,
        notes=f"{criteria_label(criteria)} · asking prices only",
        criteria_hash=c_hash,
    )

    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        existing.snapshot_date = _utcnow()
        snap = existing
    else:
        snap = MarketSnapshot(snapshot_date=_utcnow(), **payload)
        db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def build_market_history(
    db: Session,
    *,
    days: int = 30,
    live_active: int | None = None,
    live_median: int | None = None,
) -> dict[str, Any]:
    """Daily active / new / removed series for the homepage stock-flow chart."""
    from app.services.search_profile import criteria_hash, get_active_criteria

    span = max(7, days)
    today = _day_start(_utcnow())
    since = today - timedelta(days=span - 1)
    until = today + timedelta(days=1)
    criteria = get_active_criteria(db)
    c_hash = criteria_hash(criteria)

    rows = list(
        db.execute(
            select(MarketSnapshot)
            .where(MarketSnapshot.snapshot_date >= since)
            .order_by(MarketSnapshot.snapshot_date.asc())
        )
        .scalars()
        .all()
    )
    by_day: dict[str, MarketSnapshot] = {}
    for row in rows:
        if row.criteria_hash not in (None, c_hash):
            continue
        key = _day_start(row.snapshot_date).date().isoformat()
        if key in by_day and by_day[key].criteria_hash == c_hash and row.criteria_hash is None:
            continue
        by_day[key] = row

    new_by_day, removed_by_day = _calendar_day_counts(
        db, criteria=criteria, since=since, until=until
    )

    day_keys: list[str] = []
    for i in range(span):
        day_keys.append((since + timedelta(days=i)).date().isoformat())

    # Prefer recorded active_count; fill gaps by walking backwards from live tip
    active_by_day: dict[str, int | None] = {}
    for key in day_keys:
        snap = by_day.get(key)
        active_by_day[key] = int(snap.active_count) if snap else None

    tip_key = day_keys[-1]
    if live_active is not None:
        active_by_day[tip_key] = live_active
    elif active_by_day[tip_key] is None:
        active_by_day[tip_key] = 0

    for i in range(span - 2, -1, -1):
        key = day_keys[i]
        if active_by_day[key] is not None:
            continue
        nxt = day_keys[i + 1]
        nxt_active = active_by_day[nxt]
        if nxt_active is None:
            continue
        # active[d] = active[d+1] - new[d+1] + removed[d+1]
        reconstructed = (
            int(nxt_active)
            - int(new_by_day.get(nxt, 0))
            + int(removed_by_day.get(nxt, 0))
        )
        active_by_day[key] = max(0, reconstructed)

    points: list[dict[str, Any]] = []
    total_new = 0
    total_removed = 0
    for key in day_keys:
        snap = by_day.get(key)
        new_n = int(new_by_day.get(key, 0))
        rem_n = int(removed_by_day.get(key, 0))
        # Prefer live calendar counts; fall back to stored snapshot values
        if key not in new_by_day and snap is not None and snap.new_count:
            new_n = int(snap.new_count)
        if key not in removed_by_day and snap is not None and snap.removed_count:
            rem_n = int(snap.removed_count)
        total_new += new_n
        total_removed += rem_n
        median_price = None
        if snap and snap.median_price:
            median_price = int(snap.median_price)
        if key == tip_key and live_median is not None:
            median_price = live_median
        points.append(
            {
                "date": key,
                "active_count": active_by_day.get(key),
                "new_count": new_n,
                "removed_count": rem_n,
                "median_price": median_price,
            }
        )

    known_active = [p for p in points if p["active_count"] is not None]
    has_flow = total_new > 0 or total_removed > 0 or len(known_active) >= 1
    label = f"Stock flow · last {span} days"
    if total_new or total_removed:
        label = f"+{total_new} new · {total_removed} gone · last {span} days"

    return {
        "days": span,
        "points": points,
        "known_points": len(known_active),
        "total_new": total_new,
        "total_removed": total_removed,
        "label": label,
        "has_history": has_flow,
    }
