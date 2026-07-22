#!/usr/bin/env python3
"""Deactivate / remove listings that fail the explicit-4x4 gate.

Especially AutoTrader SEO URLs without ``4x4`` in the path
(e.g. /car-for-sale/toyota/fortuner/2.8gd-6/28658500).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, or_, select

from app.collectors.autotrader import AutoTraderCollector
from app.collectors.cars_co_za import CarsCoZaCollector
from app.db.session import SessionLocal, init_db
from app.models.entities import (
    AlertLog,
    CanonicalVehicle,
    DuplicateMatchEvidence,
    ListingObservation,
    ListingStatus,
    PriceEvent,
    ShortlistEntry,
    SourceListing,
)
from app.services.ingestion import IngestionService


def _autotrader_lacks_4x4(row: SourceListing) -> bool:
    if row.source != "autotrader":
        return False
    return AutoTraderCollector.drivetrain_from_url(row.url) != "4x4"


def _cars_lacks_4x4(row: SourceListing) -> bool:
    if row.source != "cars_co_za":
        return False
    return (
        CarsCoZaCollector._drivetrain_from_text(row.url, row.title, row.variant_raw) != "4x4"
    )


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = list(db.execute(select(SourceListing)).scalars().all())
        bad = [r for r in rows if _autotrader_lacks_4x4(r) or _cars_lacks_4x4(r)]
        bad_ids = [r.id for r in bad if r.id is not None]
        for r in bad[:30]:
            print(f"  drop {r.source}/{r.source_listing_id}  {r.url}")
        if len(bad) > 30:
            print(f"  … and {len(bad) - 30} more")

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

        # Also run criteria purge for anything else stale
        service = IngestionService(db)
        deactivated = service._purge_listings_failing_criteria()

        removed_vehicles = 0
        for vehicle in list(db.execute(select(CanonicalVehicle)).scalars().all()):
            linked = [
                x
                for x in (vehicle.source_listings or [])
                if (x.listing_status or "")
                in {
                    ListingStatus.ACTIVE.value,
                    ListingStatus.RELISTED.value,
                }
            ]
            has_4x4 = any((x.drivetrain or "").lower() == "4x4" for x in linked)
            if linked and has_4x4:
                vehicle.is_active = True
                continue
            vehicle.is_active = False
            if not linked:
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
                removed_vehicles += 1

        service._scrub_all_price_aggregates()
        db.commit()
        print(
            f"Deleted {len(bad_ids)} non-4x4 source listing(s); "
            f"criteria-deactivated {deactivated}; "
            f"removed {removed_vehicles} orphan vehicle(s)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
