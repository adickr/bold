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


def test_autotrader_keeps_4x4_when_title_has_it_even_if_slug_omits():
    """Slug /2.8gd-6/{id} is fine if variant/title explicitly says 4x4."""
    c = AutoTraderCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c._annotate_search_scope(
        [
            ListingPayload(
                source="autotrader",
                source_listing_id="28406720",
                url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6/28406720",
                title="Toyota Fortuner 2.8GD-6 4x4 VX",
                variant_raw="2.8GD-6 4x4 VX",
                make="Toyota",
                model="Fortuner",
            )
        ]
    )
    assert len(rows) == 1
    assert rows[0].drivetrain == "4x4"


def test_autotrader_drops_when_neither_slug_nor_title_says_4x4():
    """Knysna 28658500-style SEO URL with no 4x4 signal must not be assumed 4x4."""
    c = AutoTraderCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c._annotate_search_scope(
        [
            ListingPayload(
                source="autotrader",
                source_listing_id="28658500",
                url="https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6/28658500",
                title="2024 Toyota Fortuner 2.8GD-6 VX",
                variant_raw="2.8GD-6 VX",
                price_zar=669900,
                mileage_km=18000,
                make="Toyota",
                model="Fortuner",
                drivetrain="4x4",  # stale search-scope tag
            )
        ]
    )
    assert rows == []


def test_autotrader_parses_embedded_search_json():
    html = r"""
    <html><body><script>
    {"results":{"pageNumber":1,"pageCount":3,"featuredTiles":[
      {"resultType":1,"listingId":28593847,
       "canonicalUrl":"/car-for-sale/toyota/fortuner/2.8gd-6/28593847",
       "imageUrl":"https://img.autotrader.co.za/46184292",
       "make":"Toyota","model":"Fortuner","variant":"2.8GD-6 4x4 VX",
       "makeModelLongVariant":"Toyota Fortuner 2.8GD-6 4x4 VX",
       "dealerName":"Frank Vos Robertson","dealerSuburbName":"Robertson",
       "summaryIcons":[
         {"text":"Used","type":4},
         {"url":"/icons/mileage.svg","text":"92\u00A0000 km","type":1},
         {"url":"/icons/transmission-automatic.svg","text":"Automatic","type":1}
       ],
       "price":"R 629\u00A0000"}
    ]},"resultCount":65}
    </script></body></html>
    """
    c = AutoTraderCollector(settings=Settings(preferred_province="Western Cape"))
    assert c._parse_result_meta(html) == (65, 3)
    rows = c.parse_search_results_json(html)
    assert len(rows) == 1
    assert rows[0].source_listing_id == "28593847"
    assert rows[0].price_zar == 629000
    assert rows[0].mileage_km == 92000
    assert "4x4" in (rows[0].variant_raw or "")
    annotated = c._annotate_search_scope(rows)
    assert annotated[0].drivetrain == "4x4"
    assert "Western Cape" in (annotated[0].dealer_location or "")


def test_autotrader_location_from_card_text():
    assert "Brackenfell" in (AutoTraderCollector._location_from_text("Dealer in Brackenfell · R699 900") or "")
    assert AutoTraderCollector._location_from_text("Sandton dealership") is None
