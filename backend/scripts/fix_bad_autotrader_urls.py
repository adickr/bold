#!/usr/bin/env python3
"""Remove AutoTrader rows with invalid or invented SEO URLs (they 503 in-browser)."""

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
from app.services.media import is_valid_marketplace_url, looks_like_invented_autotrader_url


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = (
            db.execute(select(SourceListing).where(SourceListing.source == "autotrader"))
            .scalars()
            .all()
        )
        bad_ids: list[int] = []
        cleared = 0
        for row in rows:
            if is_valid_marketplace_url("autotrader", row.url) and not looks_like_invented_autotrader_url(
                row.url
            ):
                continue
            # Prefer delete unusable outbound links so re-collect can reinsert clean ones
            bad_ids.append(row.id)
            cleared += 1

        print(f"Deleting {cleared} AutoTrader listings with bad/invented URLs")
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
            linked = list(vehicle.source_listings or [])
            if linked:
                # Recompute active flag if all AT links gone
                vehicle.is_active = any(
                    x.listing_status
                    in {ListingStatus.ACTIVE.value, ListingStatus.RELISTED.value}
                    for x in linked
                )
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
        print(f"Removed {removed} orphan vehicles — re-collect live to restore AutoTrader links")
    finally:
        db.close()


if __name__ == "__main__":
    main()
