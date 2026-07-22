"""Criteria and ingestion / dedup tests."""

from app.collectors import get_collector
from app.config import get_settings
from app.schemas.listings import ListingPayload
from app.services.criteria import evaluate_listing
from app.services.ingestion import IngestionService


def test_excludes_4x2_and_accepts_4x4():
    bad = ListingPayload(
        source="t",
        source_listing_id="1",
        url="http://x",
        title="2021 Toyota Fortuner 2.8 4x2 VX",
        price_zar=600000,
        mileage_km=50000,
        drivetrain="4x2",
    )
    good = ListingPayload(
        source="t",
        source_listing_id="2",
        url="http://x",
        title="2021 Toyota Fortuner 2.8 4x4 VX",
        price_zar=600000,
        mileage_km=50000,
        drivetrain="4x4",
    )
    unclear = ListingPayload(
        source="t",
        source_listing_id="1b",
        url="http://x",
        title="2021 Toyota Fortuner 2.8 VX",
        price_zar=600000,
        mileage_km=50000,
        drivetrain=None,
    )
    assert evaluate_listing(bad).accepted is False
    assert evaluate_listing(good).accepted is True
    assert evaluate_listing(unclear).accepted is False


def test_stretch_price_flagged():
    listing = ListingPayload(
        source="t",
        source_listing_id="3",
        url="http://x",
        title="2023 Toyota Fortuner GR-S 4x4",
        price_zar=720000,
        mileage_km=30000,
        drivetrain="4x4",
    )
    result = evaluate_listing(listing)
    assert result.accepted is True
    assert result.is_stretch is True


def test_high_price_not_rejected():
    listing = ListingPayload(
        source="t",
        source_listing_id="3b",
        url="http://x",
        title="2024 Toyota Fortuner VX 4x4",
        price_zar=950000,
        mileage_km=15000,
        drivetrain="4x4",
    )
    result = evaluate_listing(listing)
    assert result.accepted is True
    assert result.is_stretch is True
    assert "above_comfort_price" in result.reasons


def test_fixture_collectors_return_listings():
    settings = get_settings()
    for source in ("autotrader", "cars_co_za", "webuycars"):
        collector = get_collector(source, settings=settings)
        with collector:
            listings = collector.search_from_fixtures()
        assert len(listings) >= 2


def test_fake_reduction_from_price_spread_is_cleared(db_session):
    """Highest ask among linked ads is not a seller price cut."""
    from sqlalchemy import select

    from app.models.entities import CanonicalVehicle, ListingStatus, SourceListing

    settings = get_settings()
    service = IngestionService(db_session, settings)

    vehicle = CanonicalVehicle(
        year=2023,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.4GD-6 4x4",
        drivetrain="4x4",
        original_price=639900,  # pollution from a different merged car
        current_lowest_price=539900,
        total_reduction_zar=100000,  # fake
        is_active=True,
    )
    db_session.add(vehicle)
    db_session.flush()
    db_session.add(
        SourceListing(
            source="autotrader",
            source_listing_id="28638215",
            url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28638215",
            title="2023 Toyota Fortuner 2.4GD-6 4x4",
            year=2023,
            make="Toyota",
            model="Fortuner",
            drivetrain="4x4",
            price_zar=539900,
            mileage_km=90560,
            dealer_location="Malmesbury, Western Cape",
            listing_status=ListingStatus.ACTIVE.value,
            canonical_vehicle_id=vehicle.id,
        )
    )
    db_session.commit()

    service._scrub_all_price_aggregates()
    db_session.commit()
    db_session.refresh(vehicle)
    assert vehicle.total_reduction_zar == 0
    assert vehicle.original_price == 539900
    assert vehicle.current_lowest_price == 539900


def test_repair_splits_same_source_over_merge(db_session):
    """Distinct AutoTrader ads must not stay glued on one canonical vehicle."""
    from sqlalchemy import select

    from app.models.entities import CanonicalVehicle, ListingStatus, SourceListing

    settings = get_settings()
    service = IngestionService(db_session, settings)

    vehicle = CanonicalVehicle(
        year=2024,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.4 GD-6 4x4 AT",
        drivetrain="4x4",
        current_lowest_price=559900,
        is_active=True,
    )
    db_session.add(vehicle)
    db_session.flush()

    for i, lid in enumerate(("AT_A", "AT_B", "AT_C")):
        db_session.add(
            SourceListing(
                source="autotrader",
                source_listing_id=lid,
                url=f"https://www.autotrader.co.za/car-for-sale/toyota/fortuner/x/{28000000 + i}",
                title=f"2024 Toyota Fortuner listing {lid}",
                year=2024,
                make="Toyota",
                model="Fortuner",
                variant_normalised="2.4 GD-6 4x4 AT",
                drivetrain="4x4" if i == 0 else None,
                price_zar=559900 + i * 1000,
                mileage_km=57588 if i == 0 else 40000 + i,
                dealer_name="Cape Gate Toyota",
                dealer_location="Western Cape",
                listing_status=ListingStatus.ACTIVE.value,
                canonical_vehicle_id=vehicle.id,
                image_urls=["https://images.example.com/shared-stock.jpg"],
            )
        )
    db_session.commit()

    split = service._repair_same_source_merges()
    db_session.commit()
    assert split == 2

    at_listings = (
        db_session.execute(select(SourceListing).where(SourceListing.source == "autotrader"))
        .scalars()
        .all()
    )
    canonical_ids = {x.canonical_vehicle_id for x in at_listings}
    assert len(canonical_ids) == 3
    assert all(cid is not None for cid in canonical_ids)


def test_ingestion_dedup_and_price_history(db_session):
    settings = get_settings()
    service = IngestionService(db_session, settings)

    at = get_collector("autotrader", settings=settings)
    with at:
        r1 = service.ingest_payloads("autotrader", at.search_from_fixtures())
    assert r1["success"] is True
    assert r1["found"] >= 2  # 4x2 excluded

    cars = get_collector("cars_co_za", settings=settings)
    with cars:
        r2 = service.ingest_payloads("cars_co_za", cars.search_from_fixtures())
    assert r2["success"] is True

    wbc = get_collector("webuycars", settings=settings)
    with wbc:
        r3 = service.ingest_payloads("webuycars", wbc.search_from_fixtures())
    assert r3["success"] is True

    from sqlalchemy import select
    from app.models.entities import CanonicalVehicle, SourceListing

    vehicles = db_session.execute(select(CanonicalVehicle)).scalars().all()
    # VIN-matched VX across 3 sources should collapse
    vin_matches = [v for v in vehicles if v.vin == "AHTZZ8CDX01234567"]
    assert len(vin_matches) == 1
    vx = vin_matches[0]
    assert vx.source_count >= 2
    assert vx.original_price is not None
    assert vx.current_lowest_price is not None
    assert vx.current_lowest_price <= vx.original_price

    # Simulate price reduction observation
    listing = (
        db_session.execute(
            select(SourceListing).where(SourceListing.source_listing_id == "28001001")
        )
        .scalar_one()
    )
    old = listing.price_zar
    reduced = ListingPayload(
        source="autotrader",
        source_listing_id="28001001",
        url=listing.url,
        title=listing.title,
        description=listing.description,
        dealer_name=listing.dealer_name,
        dealer_phone=listing.dealer_phone,
        dealer_location=listing.dealer_location,
        dealer_stock_number=listing.dealer_stock_number,
        year=listing.year,
        variant_raw=listing.variant_raw,
        price_zar=old - 20000,
        mileage_km=listing.mileage_km,
        colour=listing.colour,
        vin=listing.vin,
        image_urls=listing.image_urls or [],
        drivetrain="4x4",
    )
    service.ingest_payloads("autotrader", [reduced], full_scan=False)
    db_session.refresh(vx)
    assert vx.total_reduction_zar >= 20000
    assert len(vx.price_events) >= 2
