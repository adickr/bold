"""URL validation helpers for collectors."""

from app.collectors.autotrader import AutoTraderCollector


def test_autotrader_detail_url_validation():
    good = "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.4gd-6/28096596"
    bad_short = "https://www.autotrader.co.za/car-for-sale/28096596"
    search = "https://www.autotrader.co.za/cars-for-sale/toyota/fortuner"
    assert AutoTraderCollector.is_detail_url(good) is True
    assert AutoTraderCollector.listing_id_from_url(good) == "28096596"
    assert AutoTraderCollector.is_detail_url(bad_short) is False
    assert AutoTraderCollector.is_detail_url(search) is False
