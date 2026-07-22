#!/usr/bin/env python3
"""Repair or delete AutoTrader rows with invalid short URLs."""

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
    PriceEvent,
    ShortlistEntry,
    SourceListing,
)
from app.services.media import is_valid_marketplace_url, normalise_listing_url


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = (
            db.execute(select(SourceListing).where(SourceListing.source == "autotrader"))
            .scalars()
            .all()
        )
        repaired = 0
        bad_ids: list[int] = []
        for row in rows:
            fixed = normalise_listing_url(
                row.source,
                row.url,
                listing_id=row.source_listing_id,
                title=row.title,
                variant=row.variant_raw or row.variant_normalised,
                year=row.year,
            )
            if fixed and is_valid_marketplace_url("autotrader", fixed):
                if fixed != row.url:
                    row.url = fixed
                    repaired += 1
                continue
            bad_ids.append(row.id)

        print(f"Repaired {repaired} AutoTrader URLs; deleting {len(bad_ids)} unrepairable")
        if bad_ids:
            db.execute(
                delete(ListingObservation).where(
                    ListingObservation.source_listing_id.in_(bad_ids)
                )
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

        vehicles = db.execute(select(CanonicalVehicle)).scalars().all()
        removed = 0
        for vehicle in vehicles:
            if vehicle.source_listings:
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
        print(f"Removed {removed} orphan vehicles")
    finally:
        db.close()


if __name__ == "__main__":
    main()
