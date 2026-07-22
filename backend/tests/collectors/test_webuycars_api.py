"""WeBuyCars PoW + API body tests."""

from app.collectors.webuycars import WeBuyCarsCollector, solve_proof_of_work
from app.config import Settings


def test_pow_solver_meets_difficulty():
    import hashlib

    challenge = "a" * 64
    difficulty = 4
    token = solve_proof_of_work(challenge, difficulty)
    nonce = bytes(int(x) for x in token.split(","))
    digest = hashlib.sha256(bytes.fromhex(challenge) + nonce).digest()
    # first difficulty bits must be zero
    assert digest[0] >> (8 - difficulty) == 0


def test_api_body_uses_q_and_4x4():
    settings = Settings(preferred_province="Western Cape")
    c = WeBuyCarsCollector(settings=settings)
    body = c.build_api_body(offset=24, size=24)
    assert body["q"] == "Toyota Fortuner"
    assert body["Make"] == ["Toyota"]
    assert body["Model"] == ["Fortuner"]
    assert body["AxleConfiguration"] == ["4X4"]
    assert body["to"] == 24
    # Nationwide collect — WC browse filter is applied in the UI, not here
    assert body["Province"] is None
    assert "Provinces" not in body


def test_browser_params_use_q():
    c = WeBuyCarsCollector(settings=Settings(preferred_province="Western Cape"))
    params = c.build_search_params()
    assert params["q"] == "Toyota Fortuner"


def test_sale_availability_reserved_is_unavailable():
    assert (
        WeBuyCarsCollector._sale_availability({"Status": "Reserved", "StockStatus": "Stock"})
        == "unavailable"
    )
    assert WeBuyCarsCollector._sale_availability({"Status": "For Sale"}) == "available"
    assert WeBuyCarsCollector._sale_availability({"Status": "Sold"}) == "unavailable"


def test_parse_api_marks_reserved_unavailable():
    c = WeBuyCarsCollector(settings=Settings())
    payload = {
        "data": [
            {
                "StockNumber": "CB2B10753",
                "OnlineDescription": "2025 Toyota Fortuner 2.8gd-6 4x4 Gr-S Auto",
                "Model": "Fortuner",
                "Make": "Toyota",
                "Variant": "2.8gd-6 4x4 Gr-S Auto",
                "Year": 2025,
                "Price": 859900,
                "Mileage": 7489,
                "AxleConfiguration": "4X4",
                "Status": "Reserved",
                "StockStatus": "Stock",
                "Province": "Western Cape",
                "BranchName": "Brackenfell",
            },
            {
                "StockNumber": "FORSALE1",
                "OnlineDescription": "2022 Toyota Fortuner 2.8 GD-6 4x4 VX",
                "Model": "Fortuner",
                "Make": "Toyota",
                "Variant": "2.8 GD-6 4x4 VX",
                "Year": 2022,
                "Price": 699900,
                "Mileage": 45000,
                "AxleConfiguration": "4X4",
                "Status": "For Sale",
                "StockStatus": "Stock",
                "Province": "Western Cape",
            },
        ]
    }
    rows = c.parse_api_json(payload)
    by_id = {r.source_listing_id: r for r in rows}
    assert by_id["CB2B10753"].availability == "unavailable"
    assert by_id["FORSALE1"].availability == "available"
