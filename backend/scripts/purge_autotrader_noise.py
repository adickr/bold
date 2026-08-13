#!/usr/bin/env python3
"""Remove AutoTrader noise rows: fake 100k km, chip titles, non-4x4 SEO slugs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, or_, select

from app.collectors.autotrader import AutoTraderCollector
from app.db.session import SessionLocal, init_db
from app.models.entities import (
    AlertLog,
    CanonicalVehicle,
    DuplicateMatchEvidence,
    ListingObservation,
    PriceEvent,
    ShortlistEntry,
    SourceListing,
)
from app.schemas.listings import ListingPayload
from app.services.criteria import evaluate_listing


def _is_noise(row: SourceListing) -> bool:
    if row.source != "autotrader":
        return False
    url = row.url or ""
    title = row.title or ""
    if AutoTraderCollector.drivetrain_from_url(url) == "4x2":
        return True
    if AutoTraderCollector._is_chip_title(title):
        return True
    payload = ListingPayload(
        source=row.source,
        source_listing_id=row.source_listing_id,
        url=url,
        title=title,
        variant_raw=row.variant_raw,
        year=row.year,
        price_zar=row.price_zar,
        mileage_km=row.mileage_km,
        dealer_location=row.dealer_location,
        dealer_name=row.dealer_name,
        drivetrain=row.drivetrain,
        make=row.make or "Toyota",
        model=row.model or "Fortuner",
    )
    if not AutoTraderCollector._is_plausible_card(payload):
        return True
    return not evaluate_listing(payload).accepted


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = (
            db.execute(select(SourceListing).where(SourceListing.source == "autotrader"))
            .scalars()
            .all()
        )
        bad_ids = [row.id for row in rows if _is_noise(row)]
        print(f"Deleting {len(bad_ids)} noisy / non-4x4 AutoTrader listings")
        if bad_ids:
            db.execute(
                delete(ListingObservation).where(ListingObservation.source_listing_id.in_(bad_ids))
            )
            db.execute(
                delete(DuplicateMatchEvidence).where(
                    or_(
                        DuplicateMatchEvidence.listing_a_id.in_(bad_ids),
                        DuplicateMatchEvidence.listing_b_id.in_(bad_ids),
                    )
                )
            )
            db.execute(delete(PriceEvent).where(PriceEvent.source_listing_id.in_(bad_ids)))
            db.execute(delete(SourceListing).where(SourceListing.id.in_(bad_ids)))

        removed = 0
        for vehicle in db.execute(select(CanonicalVehicle)).scalars().all():
            linked = list(vehicle.source_listings or [])
            if linked:
                vehicle.is_active = any(
                    (x.listing_status or "").lower() in {"active", "relisted"} for x in linked
                )
                dts = {x.drivetrain for x in linked if x.drivetrain}
                if "4x2" in dts and "4x4" not in dts:
                    vehicle.drivetrain = "4x2"
                    vehicle.is_active = False
                continue
            if vehicle.shortlist_entry:
                db.delete(vehicle.shortlist_entry)
            db.execute(delete(AlertLog).where(AlertLog.canonical_vehicle_id == vehicle.id))
            db.execute(
                delete(DuplicateMatchEvidence).where(
                    DuplicateMatchEvidence.canonical_vehicle_id == vehicle.id
                )
            )
            db.execute(delete(PriceEvent).where(PriceEvent.canonical_vehicle_id == vehicle.id))
            db.execute(
                delete(ShortlistEntry).where(ShortlistEntry.canonical_vehicle_id == vehicle.id)
            )
            db.delete(vehicle)
            removed += 1
        db.commit()
        print(f"Removed {removed} orphan vehicles — re-collect live AutoTrader (4x4 search)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
