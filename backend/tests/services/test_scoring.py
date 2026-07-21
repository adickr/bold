"""Deal / motivation scoring tests."""

from datetime import datetime, timedelta, timezone

from app.models.entities import CanonicalVehicle
from app.services.scoring import compute_deal_score, compute_motivation_score


def test_deal_score_breakdown_sums_transparently():
    vehicle = CanonicalVehicle(
        year=2022,
        trim="VX",
        current_lowest_price=650000,
        current_mileage_km=40000,
        total_reduction_zar=30000,
        days_tracked=25,
        first_seen_at=datetime.now(timezone.utc) - timedelta(days=25),
        risk_flags=[],
        comparable_stats={"median_price": 700000},
    )
    score, breakdown = compute_deal_score(vehicle, comparable_median=700000)
    assert 0 <= score <= 100
    assert "price_value" in breakdown
    assert breakdown["inferred"] is True


def test_motivation_estimate_categories():
    vehicle = CanonicalVehicle(
        days_tracked=70,
        total_reduction_zar=40000,
        source_count=3,
        last_reduction_at=datetime.now(timezone.utc) - timedelta(days=3),
        first_seen_at=datetime.now(timezone.utc) - timedelta(days=70),
    )
    score, level, breakdown = compute_motivation_score(vehicle, dealer_similar_count=3)
    assert level in {"low", "moderate", "high", "very_high"}
    assert breakdown["estimate_only"] is True
    assert score >= 50
