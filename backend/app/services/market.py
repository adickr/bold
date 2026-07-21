"""Comparable market analysis helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CanonicalVehicle


def _compatible(a: CanonicalVehicle, b: CanonicalVehicle) -> bool:
    if a.id == b.id:
        return False
    if not b.is_active:
        return False
    if a.drivetrain and b.drivetrain and a.drivetrain != b.drivetrain:
        return False
    # Avoid comparing basic 2.4 with GR-S / VX without adjustment — require similar trim tier
    high = {"GR-S", "VX"}
    a_high = (a.trim or "") in high
    b_high = (b.trim or "") in high
    if a_high != b_high:
        return False
    if a.trim and b.trim and a.trim != b.trim:
        # Allow GD-6 family soft matches only within non-high
        if a_high or b_high:
            return False
    if a.year and b.year and abs(a.year - b.year) > 2:
        return False
    if a.engine and b.engine and a.engine != b.engine:
        # 2.8 vs 2.4 should not mix
        if ("2.8" in a.engine) != ("2.8" in b.engine):
            return False
    return True


def compute_comparable_stats(
    db: Session, vehicle: CanonicalVehicle
) -> dict[str, Any]:
    others = db.execute(select(CanonicalVehicle).where(CanonicalVehicle.is_active.is_(True))).scalars().all()
    comps = [v for v in others if _compatible(vehicle, v) and v.current_lowest_price]
    prices = [v.current_lowest_price for v in comps if v.current_lowest_price is not None]
    mileages = [v.current_mileage_km for v in comps if v.current_mileage_km is not None]

    stats: dict[str, Any] = {
        "comparable_count": len(comps),
        "median_price": int(median(prices)) if prices else None,
        "mean_price": round(mean(prices), 2) if prices else None,
        "disclaimer": "Based on asking prices, not confirmed selling prices.",
        "inferred": True,
    }

    if prices and vehicle.current_lowest_price is not None:
        below = sum(1 for p in prices if p <= vehicle.current_lowest_price)
        stats["price_percentile"] = round(100 * below / len(prices), 1)
        stats["diff_from_median"] = vehicle.current_lowest_price - stats["median_price"]
        # crude fair range: median +/- 7%
        mid = stats["median_price"]
        stats["fair_range"] = {"low": int(mid * 0.93), "high": int(mid * 1.07)}

    if mileages and vehicle.current_mileage_km is not None:
        below_m = sum(1 for m in mileages if m <= vehicle.current_mileage_km)
        stats["mileage_percentile"] = round(100 * below_m / len(mileages), 1)

    if prices and mileages:
        ppk = [p / max(m, 1) for p, m in zip(
            [v.current_lowest_price for v in comps if v.current_lowest_price and v.current_mileage_km],
            [v.current_mileage_km for v in comps if v.current_lowest_price and v.current_mileage_km],
        )]
        if ppk:
            stats["price_per_km_range"] = {
                "low": round(min(ppk), 2),
                "high": round(max(ppk), 2),
            }

    return stats


def refresh_market_fields(vehicle: CanonicalVehicle) -> None:
    if vehicle.first_seen_at:
        first = vehicle.first_seen_at
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        vehicle.days_tracked = max(0, (datetime.now(timezone.utc) - first).days)
