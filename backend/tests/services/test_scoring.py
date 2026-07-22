"""Deal / motivation scoring tests."""

from datetime import datetime, timedelta, timezone

from app.models.entities import CanonicalVehicle
from app.services.scoring import compute_deal_score, compute_motivation_score


def _base_vehicle(**kwargs):
    defaults = dict(
        year=2022,
        current_lowest_price=650000,
        current_mileage_km=40000,
        total_reduction_zar=30000,
        days_tracked=25,
        first_seen_at=datetime.now(timezone.utc) - timedelta(days=25),
        risk_flags=[],
        comparable_stats={"median_price": 700000},
        primary_dealer="Test Dealer",
        engine="2.8",
        variant_normalised="2.8 GD-6 4x4",
    )
    defaults.update(kwargs)
    return CanonicalVehicle(**defaults)


def test_deal_score_breakdown_sums_transparently():
    vehicle = _base_vehicle(trim="VX")
    score, breakdown = compute_deal_score(vehicle, comparable_median=700000)
    assert 0 <= score <= 100
    assert "price_value" in breakdown
    assert "completeness" in breakdown
    assert "specification" not in breakdown
    assert breakdown["inferred"] is True


def test_deal_score_does_not_prioritise_vx_or_grs():
    """Same facts → same score regardless of trim."""
    common = dict(
        year=2021,
        current_lowest_price=520000,
        current_mileage_km=55000,
        total_reduction_zar=0,
        days_tracked=20,
        first_seen_at=datetime.now(timezone.utc) - timedelta(days=20),
        risk_flags=[],
        primary_dealer="WBC",
        engine="2.4",
        variant_normalised="2.4gd-6 4x4",
    )
    gd6, gd6_b = compute_deal_score(_base_vehicle(trim=None, **common), comparable_median=550000)
    vx, vx_b = compute_deal_score(_base_vehicle(trim="VX", **common), comparable_median=550000)
    grs, grs_b = compute_deal_score(
        _base_vehicle(trim="GR-S", special_edition="GR-S", **common),
        comparable_median=550000,
    )
    assert gd6 == vx == grs
    assert gd6_b["completeness"] == vx_b["completeness"] == grs_b["completeness"]


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
