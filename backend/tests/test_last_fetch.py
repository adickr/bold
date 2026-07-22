"""Last-fetch summary for the dashboard."""

from datetime import datetime, timedelta, timezone

from app.api.queries import build_last_fetch_summary
from app.models.entities import CanonicalVehicle, CollectorRun, ListingStatus, PriceEvent, SourceListing


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
    db_session.add(vehicle)
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

    summary = build_last_fetch_summary(db_session)
    assert summary is not None
    assert summary["new"] == 3
    assert summary["updated"] == 3
    assert summary["price_cuts"] == 1
    assert summary["has_changes"] is True
    assert "new" in summary["summary"]
    assert summary["highlights"]
    assert summary["changes"]["new"]
    assert summary["changes"]["price_cuts"]
    assert summary["changes"]["new"][0]["href"].startswith("/vehicles/")
    assert summary["changes"]["price_cuts"][0]["change_label"].startswith("−R")


def test_last_fetch_summary_no_runs(db_session):
    assert build_last_fetch_summary(db_session) is None
