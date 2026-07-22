"""Marketplace URL repair helpers."""

from app.services.media import (
    is_valid_marketplace_url,
    normalise_listing_url,
    rebuild_autotrader_url,
)


def test_rebuild_short_autotrader_url():
    url = rebuild_autotrader_url("28638215", title="2023 Toyota Fortuner 2.4GD-6 4x4")
    assert url is not None
    assert "28638215" in url
    assert "/car-for-sale/toyota/fortuner/" in url
    assert is_valid_marketplace_url("autotrader", url)


def test_normalise_repairs_short_autotrader_path():
    fixed = normalise_listing_url(
        "autotrader",
        "https://www.autotrader.co.za/car-for-sale/28638215",
        listing_id="28638215",
        title="2023 Toyota Fortuner 2.4GD-6 4x4",
        variant="2.4GD-6 4x4",
    )
    assert fixed
    assert is_valid_marketplace_url("autotrader", fixed)
    assert fixed.endswith("/28638215")


def test_rejects_non_numeric_autotrader_fixture_id():
    assert (
        normalise_listing_url(
            "autotrader",
            "https://www.autotrader.co.za/car-for-sale/AT1001",
            listing_id="AT1001",
        )
        is None
    )


def test_webuycars_buy_a_car_shape():
    fixed = normalise_listing_url(
        "webuycars",
        "https://www.webuycars.co.za/vehicle/WBC3001",
        listing_id="WBC3001",
    )
    assert fixed == "https://www.webuycars.co.za/buy-a-car/WBC3001"
    assert is_valid_marketplace_url("webuycars", fixed)
