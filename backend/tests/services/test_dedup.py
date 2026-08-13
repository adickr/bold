"""Dedup scoring — same-source listings must not collapse into one car."""

from types import SimpleNamespace

from app.config import Settings
from app.services.dedup import score_pair, should_auto_merge, strong_identity_match


def _listing(**kwargs):
    defaults = dict(
        source="autotrader",
        source_listing_id="1",
        vin=None,
        registration=None,
        dealer_stock_number=None,
        image_phashes=None,
        image_urls=[],
        dealer_name="Cape Gate Toyota",
        mileage_km=50000,
        year=2024,
        variant_normalised="2.4 GD-6 4x4 AT",
        colour="white",
        price_zar=560000,
        dealer_phone=None,
        drivetrain="4x4",
        description=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_same_source_different_id_blocked_without_identity():
    a = _listing(source_listing_id="AT1", image_urls=["https://cdn/a.jpg", "https://cdn/b.jpg"])
    b = _listing(
        source_listing_id="AT2",
        image_urls=["https://cdn/a.jpg", "https://cdn/b.jpg"],
        mileage_km=57588,
        price_zar=559900,
    )
    result = score_pair(a, b, Settings())
    assert result.score == 0
    assert not should_auto_merge(result, Settings())
    assert any(s["signal"] == "same_source_different_id" for s in result.evidence["signals"])


def test_same_source_merges_with_shared_vin():
    a = _listing(source_listing_id="AT1", vin="AHTZZ8CDX01234567")
    b = _listing(source_listing_id="AT2", vin="AHTZZ8CDX01234567", mileage_km=45200)
    result = score_pair(a, b, Settings())
    assert strong_identity_match(a, b)
    assert should_auto_merge(result, Settings())


def test_cross_source_still_merges_on_vin():
    a = _listing(source="autotrader", source_listing_id="AT1", vin="AHTZZ8CDX01234567")
    b = _listing(source="cars_co_za", source_listing_id="C1", vin="AHTZZ8CDX01234567")
    result = score_pair(a, b, Settings())
    assert should_auto_merge(result, Settings())


def test_cross_source_same_dealer_exact_mileage_auto_merges():
    """AT + Cars.co.za: same dealer, year, variant, price, exact km → one car."""
    a = _listing(
        source="autotrader",
        source_listing_id="28500001",
        dealer_name="CFAO Mobility Toyota Tokai",
        mileage_km=38622,
        price_zar=579000,
        year=2024,
        variant_normalised="2.4 GD-6 4x4 AT",
        drivetrain="4x4",
        colour=None,
    )
    b = _listing(
        source="cars_co_za",
        source_listing_id="C9001",
        dealer_name="CFAO Mobility Toyota Tokai",
        mileage_km=38622,
        price_zar=579000,
        year=2024,
        variant_normalised="2.4 GD-6 4x4 AT",
        drivetrain="4X4",
        colour=None,
    )
    result = score_pair(a, b, Settings())
    assert should_auto_merge(result, Settings())
    assert result.score >= 75
    signals = {s["signal"] for s in result.evidence["signals"]}
    assert "same_dealer_exact_mileage" in signals


def test_dealer_suffix_still_matches():
    a = _listing(source="autotrader", source_listing_id="A1", dealer_name="Cape Gate Toyota (Pty) Ltd")
    b = _listing(source="cars_co_za", source_listing_id="C1", dealer_name="Cape Gate Toyota")
    result = score_pair(a, b, Settings())
    assert any(s["signal"] == "same_dealer_exact_mileage" for s in result.evidence["signals"])
    assert should_auto_merge(result, Settings())
