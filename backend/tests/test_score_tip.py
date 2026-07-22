"""Deal score breakdown is available for table hover tips."""

from datetime import datetime, timedelta, timezone

from app.api.queries import vehicle_to_dict
from app.models.entities import CanonicalVehicle
from app.services.scoring import compute_deal_score


def test_vehicle_to_dict_includes_deal_score_breakdown(db_session):
    vehicle = CanonicalVehicle(
        year=2022,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=599900,
        current_mileage_km=45000,
        total_reduction_zar=20000,
        days_tracked=20,
        is_active=True,
        first_seen_at=datetime.now(timezone.utc) - timedelta(days=20),
        last_seen_at=datetime.now(timezone.utc),
        primary_dealer="Test Dealer",
        engine="2.8",
        risk_flags=[],
    )
    score, breakdown = compute_deal_score(vehicle, comparable_median=650000)
    vehicle.deal_score = score
    vehicle.deal_score_breakdown = breakdown
    db_session.add(vehicle)
    db_session.commit()

    data = vehicle_to_dict(vehicle)
    assert data["deal_score"] == score
    assert data["deal_score_breakdown"]["price_value"] is not None
    assert data["deal_score_breakdown"]["total"] == score
    assert "completeness" in data["deal_score_breakdown"]
