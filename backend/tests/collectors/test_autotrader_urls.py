"""URL validation helpers for collectors."""

from app.collectors.autotrader import AutoTraderCollector
from app.config import Settings
from app.schemas.listings import ListingPayload


def test_autotrader_detail_url_validation():
    good = "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28096596"
    bad_short = "https://www.autotrader.co.za/car-for-sale/28096596"
    search = "https://www.autotrader.co.za/cars-for-sale/toyota/fortuner"
    assert AutoTraderCollector.is_detail_url(good) is True
    assert AutoTraderCollector.listing_id_from_url(good) == "28096596"
    assert AutoTraderCollector.is_detail_url(bad_short) is False
    assert AutoTraderCollector.is_detail_url(search) is False


def test_autotrader_mileage_ignores_filter_chip():
    text = "Up to 100 000 km\n2022 Toyota Fortuner 2.8GD-6 4x4\nR 699 900\n45 200 km\nBrackenfell"
    assert AutoTraderCollector._extract_mileage(text) == 45200
    assert AutoTraderCollector._extract_price(text) == 699900


def test_autotrader_mileage_ignores_lonely_100k_chip():
    text = "4x4 AT\nR 439 900\n100 000 km\nBellville, Western Cape"
    assert AutoTraderCollector._extract_mileage(text) is None


def test_autotrader_rejects_chip_noise_and_4x2_slug():
    noise = ListingPayload(
        source="autotrader",
        source_listing_id="28638215",
        url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/4x4-at/28638215",
        title="4x4 AT",
        price_zar=439900,
        mileage_km=100000,
        drivetrain="4x4",
        make="Toyota",
        model="Fortuner",
    )
    assert AutoTraderCollector._is_plausible_card(noise) is False

    four_by_two = ListingPayload(
        source="autotrader",
        source_listing_id="28638216",
        url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6-4x2/28638216",
        title="2019 Toyota Fortuner 2.4 GD-6 4x2",
        price_zar=439900,
        mileage_km=80000,
        make="Toyota",
        model="Fortuner",
    )
    assert AutoTraderCollector.drivetrain_from_url(four_by_two.url) == "4x2"
    assert AutoTraderCollector._is_plausible_card(four_by_two) is False

    good = ListingPayload(
        source="autotrader",
        source_listing_id="28638217",
        url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6-4x4/28638217",
        title="2019 Toyota Fortuner 2.4 GD-6 4x4",
        price_zar=439900,
        mileage_km=90500,
        make="Toyota",
        model="Fortuner",
    )
    assert AutoTraderCollector._is_plausible_card(good) is True
    assert AutoTraderCollector.drivetrain_from_url(good.url) == "4x4"


def test_autotrader_price_ignores_fortuner_trailing_r():
    text = "2023 Toyota Fortuner 2.4 GD-6 4x4 AT R 539 900 90 560 km"
    assert AutoTraderCollector._extract_price(text) == 539900
    assert AutoTraderCollector._extract_mileage(text) == 90560


def test_autotrader_wc_annotate_fills_empty_location():
    c = AutoTraderCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c._annotate_search_scope(
        [
            ListingPayload(
                source="autotrader",
                source_listing_id="28096596",
                url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6-4x4-vx/28096596",
                title="2022 Toyota Fortuner 2.8GD-6 4x4 VX",
                make="Toyota",
                model="Fortuner",
            )
        ]
    )
    assert rows[0].dealer_location == "Western Cape"
    assert rows[0].drivetrain == "4x4"


def test_autotrader_location_from_card_text():
    assert "Brackenfell" in (AutoTraderCollector._location_from_text("Dealer in Brackenfell · R699 900") or "")
    assert AutoTraderCollector._location_from_text("Sandton dealership") is None
