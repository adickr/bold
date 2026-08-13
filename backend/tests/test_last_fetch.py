"""Last-fetch summary for the dashboard."""

from datetime import datetime, timedelta, timezone

from app.api.queries import build_last_fetch_summary
from app.models.entities import (
    CanonicalVehicle,
    CollectorRun,
    ListingObservation,
    ListingStatus,
    PriceEvent,
    SourceListing,
)


def test_last_fetch_summary_reports_changes(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(
        CollectorRun(
            source="autotrader",
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=1),
            success=True,
            listings_found=12,
            listings_new=2,
            listings_updated=3,
        )
    )
    db_session.add(
        CollectorRun(
            source="webuycars",
            started_at=now - timedelta(minutes=4),
            finished_at=now - timedelta(seconds=30),
            success=True,
            listings_found=5,
            listings_new=1,
            listings_updated=0,
        )
    )
    vehicle = CanonicalVehicle(
        year=2022,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4 VX",
        drivetrain="4x4",
        current_lowest_price=599900,
        is_active=True,
        first_seen_at=now - timedelta(minutes=2),
        last_seen_at=now,
    )
    existing = CanonicalVehicle(
        year=2021,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.4 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=549900,
        current_mileage_km=62000,
        primary_location="Bellville, Western Cape",
        is_active=True,
        first_seen_at=now - timedelta(days=10),
        last_seen_at=now,
    )
    db_session.add_all([vehicle, existing])
    db_session.flush()
    db_session.add(
        SourceListing(
            source="autotrader",
            source_listing_id="AT1",
            url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6-4x4/28000001",
            title="2022 Toyota Fortuner 2.8GD-6 4x4 VX",
            year=2022,
            price_zar=599900,
            drivetrain="4x4",
            listing_status=ListingStatus.ACTIVE.value,
            canonical_vehicle_id=vehicle.id,
            first_seen_at=now - timedelta(minutes=2),
            last_seen_at=now,
        )
    )
    existing_listing = SourceListing(
        source="cars_co_za",
        source_listing_id="CC1",
        url="https://www.cars.co.za/for-sale/toyota/fortuner/123",
        title="2021 Toyota Fortuner 2.4 GD-6 4x4",
        year=2021,
        price_zar=549900,
        mileage_km=62000,
        drivetrain="4x4",
        listing_status=ListingStatus.ACTIVE.value,
        canonical_vehicle_id=existing.id,
        first_seen_at=now - timedelta(days=10),
        last_seen_at=now,
    )
    db_session.add(existing_listing)
    db_session.flush()
    db_session.add(
        ListingObservation(
            source_listing_id=existing_listing.id,
            observed_at=now - timedelta(days=2),
            price_zar=549900,
            mileage_km=58000,
            title="2021 Toyota Fortuner 2.4 GD-6 4x4",
            dealer_name="Old Dealer",
            source="cars_co_za",
            availability_status=ListingStatus.ACTIVE.value,
            changed_fields=[],
        )
    )
    db_session.add(
        ListingObservation(
            source_listing_id=existing_listing.id,
            observed_at=now - timedelta(minutes=2),
            price_zar=549900,
            mileage_km=62000,
            title="2021 Toyota Fortuner 2.4 GD-6 4x4",
            dealer_name="New Dealer Cape",
            source="cars_co_za",
            availability_status=ListingStatus.ACTIVE.value,
            changed_fields=["mileage_km", "dealer_name"],
        )
    )
    db_session.add(
        PriceEvent(
            canonical_vehicle_id=vehicle.id,
            observed_at=now - timedelta(minutes=1),
            old_price_zar=619900,
            new_price_zar=599900,
            change_zar=-20000,
            source="autotrader",
            note="price_change",
        )
    )
    db_session.commit()

    gone = CanonicalVehicle(
        year=2020,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=489900,
        primary_location="Stellenbosch, Western Cape",
        is_active=False,
        first_seen_at=now - timedelta(days=14),
        last_seen_at=now - timedelta(minutes=3),
        days_tracked=14,
    )
    db_session.add(gone)
    db_session.flush()
    db_session.add(
        SourceListing(
            source="autotrader",
            source_listing_id="AT_GONE",
            url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6-4x4/28000999",
            title="2020 Toyota Fortuner 2.8GD-6 4x4",
            year=2020,
            price_zar=489900,
            drivetrain="4x4",
            dealer_location="Stellenbosch, Western Cape",
            listing_status=ListingStatus.REMOVED.value,
            canonical_vehicle_id=gone.id,
            first_seen_at=now - timedelta(days=14),
            last_seen_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(minutes=2),
        )
    )
    db_session.commit()

    summary = build_last_fetch_summary(db_session)
    assert summary is not None
    assert summary["new"] == 3
    assert summary["updated"] == 3
    assert summary["price_cuts"] == 1
    assert summary["gone"] == 1
    assert summary["has_changes"] is True
    assert summary["has_material"] is True
    assert summary["has_updates"] is True
    assert "new" in summary["summary"]
    assert "gone" in summary["summary"]
    assert "updated" not in summary["summary"]
    assert "updated" in summary["full_summary"]
    assert summary["updates_label"].startswith("Browse updates")
    assert summary["highlights"]
    assert all(h["kind"] in {"new", "cut", "gone"} for h in summary["highlights"])
    assert summary["changes"]["new"]
    assert summary["changes"]["price_cuts"]
    assert summary["changes"]["gone"]
    assert summary["changes"]["gone"][0]["days_listed"] in {13, 14}
    assert summary["changes"]["gone_recent"]
    assert summary["changes"]["new"][0]["href"].startswith("/vehicles/")
    assert "deal_score" in summary["changes"]["new"][0]
    assert summary["changes"]["price_cuts"][0]["change_label"].startswith("−R")
    updates = summary["changes"]["updates"]
    assert updates
    assert updates[0]["id"] == existing.id
    assert "Mileage" in updates[0]["change_summary"]
    assert "Dealer" in updates[0]["change_summary"]


def test_last_fetch_summary_no_runs(db_session):
    assert build_last_fetch_summary(db_session) is None


def test_listing_duration_stats(db_session):
    from app.api.queries import build_listing_duration_stats

    now = datetime.now(timezone.utc)
    active = CanonicalVehicle(
        year=2023,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.4 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=579000,
        current_mileage_km=40000,
        primary_location="Cape Town, Western Cape",
        is_active=True,
        days_tracked=21,
        first_seen_at=now - timedelta(days=21),
        last_seen_at=now,
    )
    gone = CanonicalVehicle(
        year=2022,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=520000,
        current_mileage_km=70000,
        primary_location="Paarl, Western Cape",
        is_active=False,
        days_tracked=10,
        first_seen_at=now - timedelta(days=18),
        last_seen_at=now - timedelta(days=1),
    )
    db_session.add_all([active, gone])
    db_session.flush()
    db_session.add(
        SourceListing(
            source="cars_co_za",
            source_listing_id="CC_ACTIVE",
            url="https://www.cars.co.za/for-sale/used/toyota-fortuner/111/",
            year=2023,
            price_zar=579000,
            mileage_km=40000,
            drivetrain="4x4",
            dealer_location="Cape Town, Western Cape",
            listing_status=ListingStatus.ACTIVE.value,
            canonical_vehicle_id=active.id,
            first_seen_at=now - timedelta(days=21),
            last_seen_at=now,
        )
    )
    db_session.add(
        SourceListing(
            source="webuycars",
            source_listing_id="WBC_GONE",
            url="https://www.webuycars.co.za/buy-a-car/WBCGONE1",
            year=2022,
            price_zar=520000,
            mileage_km=70000,
            drivetrain="4x4",
            dealer_location="Paarl, Western Cape",
            listing_status=ListingStatus.REMOVED.value,
            canonical_vehicle_id=gone.id,
            first_seen_at=now - timedelta(days=18),
            last_seen_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
    )
    db_session.commit()

    stats = build_listing_duration_stats(db_session)
    assert stats["median_days_listed"] == 21
    assert stats["median_days_to_gone"] == 17
    assert stats["gone_sample_size"] == 1
