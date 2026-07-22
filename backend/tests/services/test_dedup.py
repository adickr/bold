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


def test_shared_stock_photos_alone_do_not_auto_merge():
    a = _listing(
        source="autotrader",
        source_listing_id="AT1",
        dealer_name="Dealer A",
        image_urls=["https://cdn/stock1.jpg", "https://cdn/stock2.jpg"],
    )
    b = _listing(
        source="cars_co_za",
        source_listing_id="C9",
        dealer_name="Dealer B",
        image_urls=["https://cdn/stock1.jpg", "https://cdn/stock2.jpg"],
        year=2022,
        variant_normalised="2.8 VX 4x4",
    )
    result = score_pair(a, b, Settings())
    assert not should_auto_merge(result, Settings())
