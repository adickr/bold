"""Daily market snapshots for dashboard history charts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import MarketSnapshot, PriceEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_start(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def record_market_snapshot(db: Session) -> MarketSnapshot:
    """Upsert today's snapshot for buyer-default matching stock."""
    from app.api.queries import default_buyer_filters, filter_vehicles

    matching = filter_vehicles(db, default_buyer_filters())
    prices = [v.current_lowest_price for v in matching if v.current_lowest_price]
    mileages = [v.current_mileage_km for v in matching if v.current_mileage_km is not None]
    days = [v.days_tracked for v in matching if v.days_tracked is not None]
    today = _day_start(_utcnow())
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

    existing = (
        db.execute(
            select(MarketSnapshot)
            .where(MarketSnapshot.snapshot_date >= today)
            .order_by(MarketSnapshot.snapshot_date.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    payload = dict(
        active_count=len(matching),
        new_count=sum(
            1
            for v in matching
            if v.first_seen_at
            and (
                v.first_seen_at
                if v.first_seen_at.tzinfo
                else v.first_seen_at.replace(tzinfo=timezone.utc)
            )
            >= _utcnow() - timedelta(hours=24)
        ),
        reduction_count=reduction_count,
        median_price=int(median(prices)) if prices else None,
        mean_price=float(mean(prices)) if prices else None,
        median_mileage=int(median(mileages)) if mileages else None,
        median_days_on_market=float(median(days)) if days else None,
        variant_breakdown=breakdown,
        notes="WC · 4x4 · ≤100k km · asking prices only",
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
    """Daily active-match series for the dashboard sparkline."""
    span = max(7, days)
    since = _day_start(_utcnow()) - timedelta(days=span - 1)
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
        key = _day_start(row.snapshot_date).date().isoformat()
        by_day[key] = row  # keep latest write for that day

    points: list[dict[str, Any]] = []
    for i in range(span):
        day = (since + timedelta(days=i)).date()
        key = day.isoformat()
        snap = by_day.get(key)
        points.append(
            {
                "date": key,
                "active_count": int(snap.active_count) if snap else None,
                "median_price": int(snap.median_price) if snap and snap.median_price else None,
            }
        )

    # Tip of the sparkline always reflects live matching count
    if points and live_active is not None:
        points[-1]["active_count"] = live_active
        if live_median is not None:
            points[-1]["median_price"] = live_median

    known = [p for p in points if p["active_count"] is not None]
    return {
        "days": span,
        "points": points,
        "known_points": len(known),
        "label": "Active matches · last 30 days",
        "has_history": len(known) >= 1,
    }
