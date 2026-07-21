"""API and HTML smoke tests."""

from app.collectors import get_collector
from app.config import get_settings
from app.services.ingestion import IngestionService


def _seed(db_session):
    settings = get_settings()
    service = IngestionService(db_session, settings)
    for source in ("autotrader", "cars_co_za", "webuycars"):
        collector = get_collector(source, settings=settings)
        with collector:
            service.ingest_payloads(source, collector.search_from_fixtures())


def test_public_health(client):
    res = client.get("/api/public/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_api_requires_auth(client):
    assert client.get("/api/dashboard").status_code == 401


def test_dashboard_and_listings(client, db_session, auth):
    _seed(db_session)
    dash = client.get("/api/dashboard", auth=auth)
    assert dash.status_code == 200
    body = dash.json()
    assert body["active_matching"] >= 1
    assert "asking prices" in body["disclaimer"].lower()

    listings = client.get("/api/vehicles", auth=auth)
    assert listings.status_code == 200
    assert len(listings.json()) >= 1

    vid = listings.json()[0]["id"]
    detail = client.get(f"/api/vehicles/{vid}", auth=auth)
    assert detail.status_code == 200
    assert "inferred" in detail.json()

    html = client.get("/", auth=auth)
    assert html.status_code == 200
    assert b"Fortuner Agent" in html.content


def test_shortlist_flow(client, db_session, auth):
    _seed(db_session)
    vehicles = client.get("/api/vehicles", auth=auth).json()
    vid = vehicles[0]["id"]
    created = client.post(
        "/api/shortlist",
        auth=auth,
        json={"canonical_vehicle_id": vid, "status": "interested", "notes": "Nice VX"},
    )
    assert created.status_code == 200
    listed = client.get("/api/shortlist", auth=auth)
    assert listed.status_code == 200
    assert len(listed.json()) >= 1


def test_html_parser_autotrader():
    from app.collectors.autotrader import AutoTraderCollector
    from pathlib import Path

    html = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "autotrader"
        / "search.html"
    ).read_text(encoding="utf-8")
    collector = AutoTraderCollector()
    parsed = collector.parse_search_html(html)
    assert len(parsed) == 1
    assert parsed[0].price_zar == 669900
