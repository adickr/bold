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
