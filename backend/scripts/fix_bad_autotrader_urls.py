#!/usr/bin/env python3
"""Delete AutoTrader rows with invalid short URLs, then optionally re-collect."""

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


def _bad_autotrader_url(url: str | None) -> bool:
    if not url:
        return True
    return not AutoTraderCollector.is_detail_url(url)


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = (
            db.execute(select(SourceListing).where(SourceListing.source == "autotrader"))
            .scalars()
            .all()
        )
        bad_ids = [r.id for r in rows if _bad_autotrader_url(r.url)]
        print(f"Found {len(bad_ids)} AutoTrader listings with invalid URLs")
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

        # Remove orphan canonicals
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
        print(f"Deleted bad listings; removed {removed} orphan vehicles")
    finally:
        db.close()


if __name__ == "__main__":
    main()
