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
    assert client.get("/health").status_code == 200


def test_login_page_public(client):
    res = client.get("/login")
    assert res.status_code == 200
    assert b"Sign in" in res.content


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

    html = client.get("/", auth=auth, follow_redirects=True)
    assert html.status_code == 200
    assert b"Fortuner Agent" in html.content

    # Form login path (clear session first)
    client.get("/logout", follow_redirects=False)
    logged_out = client.get("/", follow_redirects=False)
    assert logged_out.status_code in {303, 307}
    assert "/login" in logged_out.headers.get("location", "")
    login = client.post(
        "/login",
        data={"username": auth[0], "password": auth[1], "next": "/"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    home = client.get("/")
    assert home.status_code == 200
    assert b"Toyota Fortuner" in home.content or b"Fortuner" in home.content


def test_live_snapshot_endpoint(client, db_session, auth):
    _seed(db_session)
    # Form login so session cookie works for /live/snapshot
    client.post(
        "/login",
        data={"username": auth[0], "password": auth[1], "next": "/"},
        follow_redirects=False,
    )
    snap = client.get("/live/snapshot")
    assert snap.status_code == 200
    body = snap.json()
    assert "vehicles" in body
    assert "stats" in body
    assert "collect" in body
    assert isinstance(body["vehicles"], list)


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


def test_vehicle_detail_page_clarifies_score_points(client, db_session, auth):
    _seed(db_session)
    client.post(
        "/login",
        data={"username": auth[0], "password": auth[1], "next": "/"},
        follow_redirects=False,
    )
    vehicles = client.get("/api/vehicles", auth=auth).json()
    vid = vehicles[0]["id"]
    page = client.get(f"/vehicles/{vid}")
    assert page.status_code == 200
    html = page.text
    assert "pts" in html
    assert "Time listed" in html
    assert "Points toward 100" in html
    assert "not days on market" in html
    assert "Facts" in html
    assert "← Listings" in html
    assert "not this listing" in html
    assert "Stock Fortuner" in html


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
    assert parsed[0].source_listing_id == "28096599"
    assert "28096599" in parsed[0].url
    assert "/toyota/fortuner/" in parsed[0].url
