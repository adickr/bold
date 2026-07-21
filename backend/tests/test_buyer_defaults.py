"""Tests for buyer default filters and sorting."""

from app.api.queries import (
    default_buyer_filters,
    location_matches_province,
    sort_vehicles,
)
from app.collectors import get_collector
from app.config import get_settings
from app.services.ingestion import IngestionService


def test_drivetrain_4x4_keeps_unclear():
    from app.api.queries import _drivetrain_matches

    assert _drivetrain_matches("4x4", "4x4")
    assert _drivetrain_matches(None, "4x4")
    assert not _drivetrain_matches("4x2", "4x4")


def test_location_matches_western_cape_towns():
    assert location_matches_province("Brackenfell, Western Cape", "Western Cape")
    assert location_matches_province("Cape Town", "Western Cape")
    assert location_matches_province("Stellenbosch", "Western Cape")
    assert not location_matches_province("Sandton, Gauteng", "Western Cape")
    assert location_matches_province("Sandton, Gauteng", "Gauteng")


def test_default_buyer_filters():
    params = default_buyer_filters()
    assert params.max_mileage == 100_000
    assert params.drivetrain == "4x4"
    assert params.province == "Western Cape"
    assert params.sort == "price_asc"


def test_api_vehicles_defaults_to_wc_4x4_price_asc(client, db_session, auth):
    settings = get_settings()
    service = IngestionService(db_session, settings)
    for source in ("autotrader", "cars_co_za", "webuycars"):
        collector = get_collector(source, settings=settings)
        with collector:
            service.ingest_payloads(source, collector.search_from_fixtures())

    res = client.get("/api/vehicles", auth=auth)
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) >= 1
    for row in rows:
        assert row["drivetrain"] == "4x4"
        assert row["mileage"] is None or row["mileage"] <= 100_000
        loc = (row["location"] or "").lower()
        assert "western cape" in loc or "cape town" in loc or "stellenbosch" in loc or "brackenfell" in loc
    prices = [r["price"] for r in rows if r["price"] is not None]
    assert prices == sorted(prices)

    wide = client.get("/api/vehicles?apply_defaults=false", auth=auth)
    assert wide.status_code == 200
    assert len(wide.json()) >= len(rows)


def test_sort_vehicles_price_asc():
    class V:
        def __init__(self, price):
            self.current_lowest_price = price
            self.current_mileage_km = None
            self.deal_score = None
            self.year = None

    ordered = sort_vehicles([V(300), V(None), V(100), V(200)], "price_asc")
    assert [v.current_lowest_price for v in ordered] == [100, 200, 300, None]
