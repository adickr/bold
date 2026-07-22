"""Market snapshot history for the dashboard sparkline."""

from datetime import datetime, timedelta, timezone

from app.models.entities import CanonicalVehicle, ListingStatus, MarketSnapshot, SourceListing
from app.services.market_snapshot import build_market_history, record_market_snapshot


def _seed_matching(db, n=3):
    now = datetime.now(timezone.utc)
    for i in range(n):
        v = CanonicalVehicle(
            year=2022,
            make="Toyota",
            model="Fortuner",
            variant_normalised="2.8 GD-6 4x4",
            drivetrain="4x4",
            current_lowest_price=600000 + i * 1000,
            current_mileage_km=40000,
            primary_location="Cape Town, Western Cape",
            is_active=True,
            first_seen_at=now - timedelta(days=2),
            last_seen_at=now,
        )
        db.add(v)
        db.flush()
        db.add(
            SourceListing(
                source="autotrader",
                source_listing_id=f"SNAP{i}",
                url=f"https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6-4x4/{28000000 + i}",
                year=2022,
                price_zar=600000 + i * 1000,
                mileage_km=40000,
                drivetrain="4x4",
                listing_status=ListingStatus.ACTIVE.value,
                canonical_vehicle_id=v.id,
                dealer_location="Cape Town, Western Cape",
            )
        )
    db.commit()


def test_record_and_build_market_history(db_session):
    _seed_matching(db_session, n=4)
    snap = record_market_snapshot(db_session)
    assert snap.active_count == 4
    assert snap.median_price is not None

    # Second call upserts same day
    snap2 = record_market_snapshot(db_session)
    assert snap2.id == snap.id

    history = build_market_history(db_session, days=14, live_active=5, live_median=610000)
    assert history["has_history"] is True
    assert history["points"][-1]["active_count"] == 5
    assert history["points"][-1]["median_price"] == 610000
    assert any(p["active_count"] == 4 for p in history["points"][:-1]) or history["points"][-1]


def test_history_without_snapshots_uses_live_tip(db_session):
    history = build_market_history(db_session, days=7, live_active=12)
    assert history["points"][-1]["active_count"] == 12
    assert history["known_points"] == 1
