#!/usr/bin/env python3
"""Remove demo/fixture listings and inactive canonical vehicles."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, or_, select

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

FIXTURE_IDS = {
    ("autotrader", "AT1001"),
    ("autotrader", "AT1002"),
    ("autotrader", "AT1003"),
    ("autotrader", "AT1004"),
    ("cars_co_za", "CC2001"),
    ("cars_co_za", "CC2002"),
    ("cars_co_za", "CC2003"),
    ("webuycars", "WBC3001"),
    ("webuycars", "WBC3002"),
    ("webuycars", "WBC3003"),
}


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        listings = db.execute(select(SourceListing)).scalars().all()
        fixture_listing_ids = [
            x.id
            for x in listings
            if (x.source, x.source_listing_id) in FIXTURE_IDS
            or "example.com" in (x.url or "")
            or str(x.source_listing_id).startswith(("AT100", "CC200", "WBC300"))
        ]
        removed_statuses = {
            ListingStatus.REMOVED.value,
            ListingStatus.POSSIBLY_REMOVED.value,
        }
        inactive_listing_ids = [
            x.id for x in listings if x.listing_status in removed_statuses
        ]
        target_listing_ids = sorted(set(fixture_listing_ids + inactive_listing_ids))

        if target_listing_ids:
            db.execute(
                delete(ListingObservation).where(
                    ListingObservation.source_listing_id.in_(target_listing_ids)
                )
            )
            db.execute(
                delete(DuplicateMatchEvidence).where(
                    or_(
                        DuplicateMatchEvidence.listing_a_id.in_(target_listing_ids),
                        DuplicateMatchEvidence.listing_b_id.in_(target_listing_ids),
                    )
                )
            )
            db.execute(
                delete(PriceEvent).where(PriceEvent.source_listing_id.in_(target_listing_ids))
            )
            db.execute(delete(SourceListing).where(SourceListing.id.in_(target_listing_ids)))

        # Drop canonical vehicles with no remaining source listings, or inactive leftovers
        vehicles = db.execute(select(CanonicalVehicle)).scalars().all()
        deleted_vehicles = 0
        for vehicle in vehicles:
            linked = [s for s in vehicle.source_listings]
            if not linked or not vehicle.is_active:
                if vehicle.shortlist_entry:
                    db.delete(vehicle.shortlist_entry)
                db.execute(
                    delete(AlertLog).where(AlertLog.canonical_vehicle_id == vehicle.id)
                )
                db.execute(
                    delete(DuplicateMatchEvidence).where(
                        DuplicateMatchEvidence.canonical_vehicle_id == vehicle.id
                    )
                )
                db.execute(delete(PriceEvent).where(PriceEvent.canonical_vehicle_id == vehicle.id))
                db.execute(
                    delete(ShortlistEntry).where(
                        ShortlistEntry.canonical_vehicle_id == vehicle.id
                    )
                )
                db.delete(vehicle)
                deleted_vehicles += 1

        db.commit()
        print(
            f"Removed {len(target_listing_ids)} source listings and "
            f"{deleted_vehicles} canonical vehicles (demo/inactive cleanup)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
