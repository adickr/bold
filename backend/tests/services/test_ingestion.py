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
    assert evaluate_listing(bad).accepted is False
    assert evaluate_listing(good).accepted is True


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


def test_fixture_collectors_return_listings():
    settings = get_settings()
    for source in ("autotrader", "cars_co_za", "webuycars"):
        collector = get_collector(source, settings=settings)
        with collector:
            listings = collector.search_from_fixtures()
        assert len(listings) >= 2


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
            select(SourceListing).where(SourceListing.source_listing_id == "AT1001")
        )
        .scalar_one()
    )
    old = listing.price_zar
    reduced = ListingPayload(
        source="autotrader",
        source_listing_id="AT1001",
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
