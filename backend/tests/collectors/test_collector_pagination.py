"""Collector search URL / pagination smoke tests."""

from app.collectors.autotrader import AutoTraderCollector
from app.collectors.cars_co_za import CarsCoZaCollector
from app.config import Settings


def test_autotrader_western_cape_url_includes_province_id():
    settings = Settings(preferred_province="Western Cape")
    c = AutoTraderCollector(settings=settings)
    assert "/western-cape/p-9/toyota/fortuner" in c.search_base_url()
    params = c.build_search_params(page=3)
    assert params["pagenumber"] == 3
    assert params["rcp"] == settings.collector_results_per_page


def test_cars_co_za_pagination_param():
    settings = Settings(preferred_province="Western Cape")
    c = CarsCoZaCollector(settings=settings)
    assert "Western-Cape" in c.search_base_url()
    assert c.build_search_params(page=4)["P"] == 4
