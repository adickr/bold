"""Collector search URL / pagination smoke tests."""

from app.collectors.autotrader import AutoTraderCollector
from app.collectors.cars_co_za import CarsCoZaCollector
from app.config import Settings


def test_autotrader_western_cape_url_includes_province_id():
    settings = Settings(preferred_province="Western Cape", max_mileage_km=100_000)
    c = AutoTraderCollector(settings=settings)
    assert c.search_base_url().endswith("/western-cape/p-9/toyota/fortuner")
    params = c.build_search_params(page=1)
    assert params == {
        "mileage": "less-than-100000",
        "transmissiondrive": "4x4",
    }
    params2 = c.build_search_params(page=3)
    assert params2["pagenumber"] == 3
    assert params2["mileage"] == "less-than-100000"
    assert params2["transmissiondrive"] == "4x4"
    # Old params that 503'd must not return
    assert "mileage_to" not in params
    assert "year" not in params
    assert "rcp" not in params


def test_cars_co_za_pagination_param():
    settings = Settings(preferred_province="Western Cape", max_mileage_km=100_000)
    c = CarsCoZaCollector(settings=settings)
    assert "usedcars" in c.SEARCH_URL
    params = c.build_search_params(page=4)
    assert params["P"] == 4
    assert params["vehicle_axle_config"] == "4X4"
    assert params["vfs_area"] == "Western Cape"
