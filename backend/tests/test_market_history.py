"""Market snapshot history and stock-flow series for the dashboard."""

from datetime import datetime, timedelta, timezone

from app.models.entities import CanonicalVehicle, ListingStatus, MarketSnapshot, SourceListing
from app.services.market_snapshot import build_market_history, record_market_snapshot
from app.services.search_profile import save_active_profile


def _seed_matching(db, n=3, *, first_seen_offset_days=2):
    now = datetime.now(timezone.utc)
    vehicles = []
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
            first_seen_at=now - timedelta(days=first_seen_offset_days),
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
        vehicles.append(v)
    db.commit()
    return vehicles


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
    assert "new_count" in history["points"][-1]
    assert "removed_count" in history["points"][-1]


def test_history_without_snapshots_uses_live_tip(db_session):
    history = build_market_history(db_session, days=7, live_active=12)
    assert history["points"][-1]["active_count"] == 12
    assert history["known_points"] >= 1


def test_stock_flow_counts_new_and_removed_by_day(db_session):
    now = datetime.now(timezone.utc)
    today = now.replace(hour=12, minute=0, second=0, microsecond=0)
    save_active_profile(
        db_session,
        province="Western Cape",
        max_mileage_km=100000,
        required_drivetrain="4x4",
    )

    # Two new today
    for i in range(2):
        v = CanonicalVehicle(
            year=2021,
            make="Toyota",
            model="Fortuner",
            variant_normalised="2.8 GD-6 4x4",
            drivetrain="4x4",
            current_lowest_price=550000,
            current_mileage_km=45000,
            primary_location="Stellenbosch, Western Cape",
            is_active=True,
            first_seen_at=today,
            last_seen_at=today,
        )
        db_session.add(v)
        db_session.flush()
        db_session.add(
            SourceListing(
                source="cars_co_za",
                source_listing_id=f"NEW{i}",
                url=f"https://www.cars.co.za/usedcars/toyota/fortuner/2021-2-8-gd-6-4x4/{100 + i}/",
                year=2021,
                price_zar=550000,
                mileage_km=45000,
                drivetrain="4x4",
                listing_status=ListingStatus.ACTIVE.value,
                canonical_vehicle_id=v.id,
                dealer_location="Stellenbosch, Western Cape",
                first_seen_at=today,
                last_seen_at=today,
            )
        )

    # One removed today
    gone = CanonicalVehicle(
        year=2020,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=480000,
        current_mileage_km=70000,
        primary_location="Bellville, Western Cape",
        is_active=False,
        first_seen_at=today - timedelta(days=10),
        last_seen_at=today - timedelta(days=1),
    )
    db_session.add(gone)
    db_session.flush()
    listing = SourceListing(
        source="webuycars",
        source_listing_id="GONE1",
        url="https://www.webuycars.co.za/vehicle/gone1",
        year=2020,
        price_zar=480000,
        mileage_km=70000,
        drivetrain="4x4",
        listing_status=ListingStatus.REMOVED.value,
        canonical_vehicle_id=gone.id,
        dealer_location="Bellville, Western Cape",
        first_seen_at=today - timedelta(days=10),
        last_seen_at=today - timedelta(days=1),
        updated_at=today,
    )
    db_session.add(listing)
    db_session.commit()

    snap = record_market_snapshot(db_session)
    assert snap.new_count == 2
    assert snap.removed_count == 1

    history = build_market_history(db_session, days=14, live_active=2)
    tip = history["points"][-1]
    assert tip["new_count"] == 2
    assert tip["removed_count"] == 1
    assert history["total_new"] >= 2
    assert history["total_removed"] >= 1
    assert "+2 new" in history["label"] or history["total_new"] >= 2


def test_stock_flow_reconstructs_active_when_snapshots_sparse(db_session):
    now = datetime.now(timezone.utc)
    # Older vehicle first seen 5 days ago
    v = CanonicalVehicle(
        year=2022,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=600000,
        current_mileage_km=30000,
        primary_location="Cape Town, Western Cape",
        is_active=True,
        first_seen_at=now - timedelta(days=5),
        last_seen_at=now,
    )
    db_session.add(v)
    db_session.flush()
    db_session.add(
        SourceListing(
            source="autotrader",
            source_listing_id="OLD1",
            url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6-4x4/29000001",
            year=2022,
            price_zar=600000,
            mileage_km=30000,
            drivetrain="4x4",
            listing_status=ListingStatus.ACTIVE.value,
            canonical_vehicle_id=v.id,
            dealer_location="Cape Town, Western Cape",
        )
    )
    db_session.commit()

    history = build_market_history(db_session, days=10, live_active=1)
    assert history["points"][-1]["active_count"] == 1
    # Day of first_seen should show a new listing
    day_key = (now - timedelta(days=5)).date().isoformat()
    point = next(p for p in history["points"] if p["date"] == day_key)
    assert point["new_count"] == 1
