"""Cars.co.za search URL / HTML parsing tests."""

from app.collectors.cars_co_za import CarsCoZaCollector
from app.config import Settings


def test_cars_search_url_matches_site_filters():
    settings = Settings(preferred_province="Western Cape", max_mileage_km=100_000)
    c = CarsCoZaCollector(settings=settings)
    params = c.build_search_params(page=2)
    assert params["make_model_variant"] == "Toyota[Fortuner]"
    assert params["vfs_area"] == "Western Cape"
    assert params["vehicle_axle_config"] == "4X4"
    assert params["vfs_mileage"] == "0-99999"
    assert params["P"] == 2
    url = c.search_url(1)
    assert "usedcars/?" in url
    assert "vehicle_axle_config=4X4" in url
    assert "Western" in url
    urls = c.search_urls(1)
    assert any("Western-Cape/Toyota/Fortuner" in u for u in urls)
    assert any("make_model_variant=" in u for u in urls)


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
    # Listing-page scrape is enough: price + mileage present without opening detail
    by_id = {r.source_listing_id: r for r in rows}
    assert by_id["11014602"].price_zar == 619995
    assert by_id["11014602"].mileage_km == 41000


def test_cars_does_not_assume_4x4_when_slug_omits_axle():
    """Tygervalley mHev demo is 4x2 on the detail page — slug has no 4x4."""
    html = """
    <div class="vehicle-card" data-vehicle-id="11098128">
      <a href="/for-sale/used/2026-Toyota-Fortuner-2.8-GD-6-Auto-mHev-Western-Cape-Tygervalley/11098128/">
        <h2>2026 Toyota Fortuner 2.8 GD-6 Auto (mHev)</h2>
      </a>
      <span class="price">R 779 900</span>
      <span>2 500 Km</span>
    </div>
    """
    c = CarsCoZaCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c._annotate(c._valid_vehicle_listings(c.parse_search_html(html)))
    assert len(rows) == 1
    assert rows[0].price_zar == 779900
    assert rows[0].drivetrain is None
    from app.services.criteria import evaluate_listing

    result = evaluate_listing(rows[0])
    assert result.accepted is False
    assert "drivetrain_unclear" in result.reasons


def test_cars_raised_body_slug_is_4x2():
    html = """
    <div class="vehicle-card" data-vehicle-id="11123133">
      <a href="/for-sale/used/2017-Toyota-Fortuner-2.4-GD-6-Raised-Body-Auto-Western-Cape-Tokai/11123133/">
        <h2>2017 Toyota Fortuner 2.4 GD-6 Raised Body Auto</h2>
      </a>
      <span class="price">R 399 000</span>
      <span>83 988 Km</span>
    </div>
    """
    c = CarsCoZaCollector(settings=Settings(preferred_province="Western Cape"))
    rows = c._annotate(c._valid_vehicle_listings(c.parse_search_html(html)))
    assert len(rows) == 1
    assert rows[0].drivetrain == "4x2"
    from app.services.criteria import evaluate_listing

    result = evaluate_listing(rows[0])
    assert result.accepted is False
    assert "4x2_excluded" in result.reasons


def test_merge_page_results_dedupes_html_and_api():
    c = CarsCoZaCollector(settings=Settings(preferred_province="Western Cape"))
    html = """
    <a href="/for-sale/used/2024-Toyota-Fortuner-2.8-Western-Cape-Bellville/10999001/">
      2024 Toyota Fortuner R 650 000 30 000 Km
    </a>
    """
    api = {
        "results": [
            {
                "id": "10999001",
                "title": "2024 Toyota Fortuner 2.8 GD-6 4x4",
                "url": "/for-sale/used/2024-Toyota-Fortuner/10999001/",
                "price": 650000,
                "mileage": 30000,
                "year": 2024,
                "model": "Fortuner",
            }
        ]
    }
    rows = c._merge_page_results(html, [api])
    assert len([r for r in rows if r.source_listing_id == "10999001"]) == 1

