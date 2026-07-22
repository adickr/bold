"""Cars.co.za search URL / HTML parsing tests."""

from app.collectors.cars_co_za import CarsCoZaCollector
from app.config import Settings
from app.services.criteria import evaluate_listing


def test_cars_search_url_matches_live_4x4_board():
    """Must match the live WC · 4x4 · ≤100k query URL — never the SEO path."""
    settings = Settings(preferred_province="Western Cape", max_mileage_km=100_000)
    c = CarsCoZaCollector(settings=settings)
    params = c.build_search_params(page=2)
    assert params["make_model_variant"] == "Toyota[Fortuner]"
    assert params["vfs_area"] == "Western Cape"
    assert params["vehicle_axle_config"] == "4X4"
    assert params["vfs_mileage"] == "0-99999"
    assert params["sort"] == "sort_rank"
    assert params["price_type"] == "listing_price"
    assert params["P"] == 2
    url = c.search_url(1)
    assert url.startswith("https://www.cars.co.za/usedcars/?")
    assert "vehicle_axle_config=4X4" in url
    assert "make_model_variant=" in url
    assert "vfs_area=Western" in url
    assert "sort=sort_rank" in url
    # SEO path leaks 4x2s — must not be used
    urls = c.search_urls(1)
    assert urls == [url]
    assert not any("Western-Cape/Toyota/Fortuner" in u for u in urls)


def test_parse_detail_anchors():
    html = """
    <html><body>
      <div class="vehicle-card" data-vehicle-id="11014602">
        <a href="/for-sale/used/2025-Toyota-Fortuner-2.4-GD-6-4x4-Auto-Western-Cape-Rondebosch/11014602/">
          <h2>2025 Toyota Fortuner 2.4 GD-6 4x4 Auto</h2>
        </a>
        <span class="price">R 619 995</span>
        <span>41 000 Km</span>
        <span class="location">Rondebosch, Western Cape</span>
      </div>
      <a href="/for-sale/used/2024-Toyota-Fortuner-2.8-GD-6-4x4-VX-Auto-Western-Cape-Bellville/10999001/">
        Toyota Fortuner 2.8
      </a>
      <a href="/news/toyota-fortuner-spotted/338011/">Next-Generation Toyota Fortuner Spotted</a>
      <p>1 - 20 of 55</p>
    </body></html>
    """
    c = CarsCoZaCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c.parse_search_html(html)
    rows = c._valid_vehicle_listings(rows)
    ids = {r.source_listing_id for r in rows}
    assert "11014602" in ids
    assert "10999001" in ids
    assert "338011" not in ids
    assert c._parse_total(html) == 55
    annotated = c._annotate(rows)
    by_ann = {r.source_listing_id: r for r in annotated}
    assert by_ann["11014602"].drivetrain == "4x4"
    assert by_ann["10999001"].drivetrain == "4x4"
    by_id = {r.source_listing_id: r for r in rows}
    assert by_id["11014602"].price_zar == 619995
    assert by_id["11014602"].mileage_km == 41000


def test_cars_drops_cards_without_explicit_4x4():
    """mHev / Raised Body / no-axle slugs must not survive annotate."""
    html = """
    <div class="vehicle-card" data-vehicle-id="11098128">
      <a href="/for-sale/used/2026-Toyota-Fortuner-2.8-GD-6-Auto-mHev-Western-Cape-Tygervalley/11098128/">
        <h2>2026 Toyota Fortuner 2.8 GD-6 Auto (mHev)</h2>
      </a>
      <span class="price">R 779 900</span>
    </div>
    <div class="vehicle-card" data-vehicle-id="11123133">
      <a href="/for-sale/used/2017-Toyota-Fortuner-2.4-GD-6-Raised-Body-Auto-Western-Cape-Tokai/11123133/">
        <h2>2017 Toyota Fortuner 2.4 GD-6 Raised Body Auto</h2>
      </a>
      <span class="price">R 399 000</span>
    </div>
    <div class="vehicle-card" data-vehicle-id="11014602">
      <a href="/for-sale/used/2025-Toyota-Fortuner-2.4-GD-6-4x4-Auto-Western-Cape-Rondebosch/11014602/">
        <h2>2025 Toyota Fortuner 2.4 GD-6 4x4 Auto</h2>
      </a>
      <span class="price">R 619 995</span>
    </div>
    """
    c = CarsCoZaCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c._annotate(c._valid_vehicle_listings(c.parse_search_html(html)))
    ids = {r.source_listing_id for r in rows}
    assert ids == {"11014602"}
    assert all(r.drivetrain == "4x4" for r in rows)
    assert evaluate_listing(rows[0]).accepted is True


def test_merge_page_results_dedupes_html_and_api():
    c = CarsCoZaCollector(settings=Settings(preferred_province="Western Cape"))
    html = """
    <a href="/for-sale/used/2024-Toyota-Fortuner-2.8-4x4-Western-Cape-Bellville/10999001/">
      2024 Toyota Fortuner 4x4 R 650 000 30 000 Km
    </a>
    """
    api = {
        "results": [
            {
                "id": "10999001",
                "title": "2024 Toyota Fortuner 2.8 GD-6 4x4",
                "url": "/for-sale/used/2024-Toyota-Fortuner-2.8-4x4/10999001/",
                "price": 650000,
                "mileage": 30000,
                "year": 2024,
                "model": "Fortuner",
            }
        ]
    }
    rows = c._annotate(c._merge_page_results(html, [api]))
    assert len([r for r in rows if r.source_listing_id == "10999001"]) == 1
    assert rows[0].drivetrain == "4x4"
