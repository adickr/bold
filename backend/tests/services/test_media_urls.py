"""Marketplace URL repair helpers."""

from app.services.media import (
    is_valid_marketplace_url,
    looks_like_invented_autotrader_url,
    normalise_listing_url,
    rebuild_autotrader_url,
)


def test_rebuild_autotrader_url_never_invents():
    assert (
        rebuild_autotrader_url(
            "28638215",
            title="2023 Toyota Fortuner 2.4GD-6 4x4",
            variant="2.4GD-6 4x4",
        )
        is None
    )


def test_short_and_invented_autotrader_urls_are_invalid():
    assert not is_valid_marketplace_url(
        "autotrader", "https://www.autotrader.co.za/car-for-sale/28638215"
    )
    assert not is_valid_marketplace_url(
        "autotrader",
        "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2-4gd-6/28638215",
    )
    assert looks_like_invented_autotrader_url(
        "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2-4gd-6/28638215"
    )
    assert is_valid_marketplace_url(
        "autotrader",
        "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28638215",
    )
    assert not looks_like_invented_autotrader_url(
        "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28638215"
    )


def test_normalise_rejects_bad_autotrader_urls():
    assert (
        normalise_listing_url(
            "autotrader",
            "https://www.autotrader.co.za/car-for-sale/28638215",
            listing_id="28638215",
            title="2023 Toyota Fortuner 2.4GD-6 4x4",
            variant="2.4GD-6 4x4",
        )
        is None
    )
    assert (
        normalise_listing_url(
            "autotrader",
            "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2-4gd-6/28638215",
            listing_id="28638215",
        )
        is None
    )
    assert (
        normalise_listing_url(
            "autotrader",
            "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28638215",
            listing_id="28638215",
        )
        == "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28638215"
    )


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
