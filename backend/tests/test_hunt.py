"""Named hunts: Fortuner 4x4 stays default; RAV4 Hybrid is a separate search."""

from __future__ import annotations

from sqlalchemy import select

from app.api.queries import default_buyer_filters, filter_vehicles, nationwide_hunt_filters
from app.collectors.autotrader import AutoTraderCollector
from app.collectors.cars_co_za import CarsCoZaCollector
from app.collectors.webuycars import WeBuyCarsCollector
from app.config import Settings
from app.models.entities import CanonicalVehicle, SearchProfile
from app.schemas.listings import ListingPayload, VehicleFilterParams
from app.services.criteria import evaluate_listing
from app.services.hunt import is_hybrid_text, looks_like_model
from app.services.market import _compatible
from app.services.search_profile import (
    activate_hunt,
    get_or_create_active_profile,
    is_hard_reject,
    list_hunts,
    save_active_profile,
)
from app.services.stock import model_shot


def _rav4_settings(**overrides) -> Settings:
    data = {
        "preferred_province": "Western Cape",
        "max_mileage_km": 100_000,
        "make": "Toyota",
        "model": "RAV4",
        "required_fuel": "hybrid",
        "required_drivetrain": "",
    }
    data.update(overrides)
    return Settings(**data)


def _listing(**overrides) -> ListingPayload:
    data = {
        "source": "autotrader",
        "source_listing_id": "1",
        "url": "https://www.autotrader.co.za/car-for-sale/toyota/fortuner/2.8gd-6-4x4-vx/28096596",
        "title": "2022 Toyota Fortuner 2.8GD-6 4x4 VX",
        "make": "Toyota",
        "model": "Fortuner",
        "year": 2022,
        "price_zar": 650000,
        "mileage_km": 45000,
        "dealer_location": "Western Cape",
        "drivetrain": "4x4",
        "fuel_type": "Diesel",
        "dealer_name": "Dealer",
    }
    data.update(overrides)
    return ListingPayload(**data)


def test_looks_like_model_rav4_aliases():
    assert looks_like_model("2021 Toyota RAV4 2.5 Hybrid", model="RAV4")
    assert looks_like_model("Toyota RAV 4 VX", model="RAV4")
    assert looks_like_model("/car-for-sale/toyota/rav4/2.5-hybrid/", model="RAV4")
    assert not looks_like_model("2022 Toyota Fortuner 2.8 4x4", model="RAV4")
    assert not looks_like_model("2021 Toyota RAV4 Hybrid", model="Fortuner")


def test_is_hybrid_text():
    assert is_hybrid_text("2.5 Hybrid GX")
    assert is_hybrid_text("RAV4 HEV E-Four")
    assert is_hybrid_text("PHEV")
    assert not is_hybrid_text("2.8 GD-6 Diesel")


def test_evaluate_fortuner_hunt_rejects_rav4():
    decision = evaluate_listing(
        _listing(
            url="https://www.autotrader.co.za/car-for-sale/toyota/rav4/2.5-hybrid-vx/28100001",
            title="2022 Toyota RAV4 2.5 Hybrid VX",
            model="RAV4",
            drivetrain=None,
            fuel_type="hybrid",
        ),
        Settings(),
    )
    assert decision.accepted is False
    assert decision.reasons == ["not_fortuner"]


def test_evaluate_rav4_hybrid_accepts_and_allows_non_4x4():
    listing = _listing(
        url="https://www.autotrader.co.za/car-for-sale/toyota/rav4/2.5-hybrid-gx/28100002",
        title="2021 Toyota RAV4 2.5 Hybrid GX",
        model="RAV4",
        drivetrain=None,
        fuel_type="hybrid",
        variant_raw="2.5 Hybrid GX",
    )
    decision = evaluate_listing(listing, _rav4_settings())
    assert decision.accepted is True
    assert "fuel_mismatch" not in decision.reasons


def test_evaluate_rav4_hunt_rejects_petrol_and_fortuner():
    petrol = evaluate_listing(
        _listing(
            url="https://www.autotrader.co.za/car-for-sale/toyota/rav4/2.0-gx/28100003",
            title="2020 Toyota RAV4 2.0 GX",
            model="RAV4",
            fuel_type="Petrol",
            variant_raw="2.0 GX",
        ),
        _rav4_settings(),
    )
    assert petrol.accepted is False
    assert petrol.reasons == ["fuel_mismatch"]

    fortuner = evaluate_listing(_listing(), _rav4_settings())
    assert fortuner.accepted is False
    assert fortuner.reasons == ["wrong_model"]


def test_hard_reject_includes_model_and_fuel():
    settings = Settings()
    assert is_hard_reject(["not_fortuner"], settings) is True
    assert is_hard_reject(["wrong_model"], settings) is True
    assert is_hard_reject(["fuel_mismatch"], settings) is True
    assert is_hard_reject(["mileage_too_high"], settings) is False


def test_ensure_hunts_keeps_fortuner_active(db_session):
    profile = get_or_create_active_profile(db_session)
    assert profile.hunt_key == "fortuner-4x4"
    assert profile.model == "Fortuner"
    assert profile.required_drivetrain == "4x4"
    rows = list(db_session.execute(select(SearchProfile)).scalars())
    keys = {row.hunt_key for row in rows}
    assert keys == {"fortuner-4x4", "rav4-hybrid"}
    assert sum(1 for row in rows if row.is_active) == 1


def test_activate_hunt_switches_without_losing_fortuner(db_session):
    fortuner = get_or_create_active_profile(db_session)
    save_active_profile(
        db_session,
        province="Gauteng",
        max_mileage_km=80000,
        required_drivetrain="4x4",
    )
    rav4 = activate_hunt(db_session, "rav4-hybrid")
    assert rav4.hunt_key == "rav4-hybrid"
    assert rav4.model == "RAV4"
    assert rav4.required_fuel == "hybrid"
    assert not rav4.required_drivetrain
    assert rav4.is_active is True

    hunts = list_hunts(db_session)
    by_key = {h["key"]: h for h in hunts}
    assert by_key["rav4-hybrid"]["active"] is True
    assert by_key["fortuner-4x4"]["active"] is False

    back = activate_hunt(db_session, "fortuner-4x4")
    assert back.id == fortuner.id
    assert back.province == "Gauteng"
    assert back.max_mileage_km == 80000
    assert back.model == "Fortuner"


def test_buyer_filters_follow_active_hunt(db_session):
    activate_hunt(db_session, "rav4-hybrid")
    params = default_buyer_filters(db_session)
    assert params.model == "RAV4"
    assert params.fuel_type == "hybrid"
    assert params.drivetrain is None
    nationwide = nationwide_hunt_filters(db_session)
    assert nationwide.model == "RAV4"
    assert nationwide.fuel_type == "hybrid"
    assert nationwide.province is None


def test_filter_vehicles_does_not_mix_models(db_session):
    db_session.add_all(
        [
            CanonicalVehicle(
                year=2022,
                make="Toyota",
                model="Fortuner",
                variant_normalised="2.8 GD-6 4x4",
                drivetrain="4x4",
                fuel_type="diesel",
                current_lowest_price=600000,
                current_mileage_km=40000,
                primary_location="Cape Town, Western Cape",
                is_active=True,
            ),
            CanonicalVehicle(
                year=2021,
                make="Toyota",
                model="RAV4",
                variant_normalised="2.5 Hybrid VX",
                drivetrain=None,
                fuel_type="hybrid",
                engine="2.5 Hybrid",
                current_lowest_price=580000,
                current_mileage_km=35000,
                primary_location="Cape Town, Western Cape",
                is_active=True,
            ),
            CanonicalVehicle(
                year=2020,
                make="Toyota",
                model="RAV4",
                variant_normalised="2.0 GX",
                fuel_type="petrol",
                current_lowest_price=420000,
                current_mileage_km=50000,
                primary_location="Cape Town, Western Cape",
                is_active=True,
            ),
        ]
    )
    db_session.commit()
    fortuners = filter_vehicles(
        db_session, VehicleFilterParams(active_only=True, model="Fortuner")
    )
    rav4_hybrids = filter_vehicles(
        db_session,
        VehicleFilterParams(active_only=True, model="RAV4", fuel_type="hybrid"),
    )
    assert {v.model for v in fortuners} == {"Fortuner"}
    assert len(fortuners) == 1
    assert {v.model for v in rav4_hybrids} == {"RAV4"}
    assert len(rav4_hybrids) == 1
    assert rav4_hybrids[0].variant_normalised == "2.5 Hybrid VX"


def test_comparables_do_not_mix_models():
    a = CanonicalVehicle(id=1, make="Toyota", model="Fortuner", is_active=True, year=2021, trim="VX")
    b = CanonicalVehicle(id=2, make="Toyota", model="RAV4", is_active=True, year=2021, trim="VX")
    c = CanonicalVehicle(id=3, make="Toyota", model="Fortuner", is_active=True, year=2022, trim="VX")
    assert _compatible(a, b) is False
    assert _compatible(a, c) is True


def test_autotrader_rav4_hybrid_search_url():
    c = AutoTraderCollector(settings=_rav4_settings())
    assert c.search_base_url().endswith("/western-cape/p-9/toyota/rav4")
    params = c.build_search_params()
    assert params["fueltype"] == "hybrid"
    assert params["mileage"] == "less-than-100000"
    assert "transmissiondrive" not in params


def test_cars_rav4_hybrid_search_url():
    c = CarsCoZaCollector(settings=_rav4_settings())
    params = c.build_search_params()
    assert params["make_model_variant"] == "Toyota[RAV4]"
    assert params["vfs_fuel_type"] == "Hybrid"
    assert params["vfs_area"] == "Western Cape"
    assert "vehicle_axle_config" not in params


def test_webuycars_rav4_hybrid_api_body():
    c = WeBuyCarsCollector(settings=_rav4_settings())
    body = c.build_api_body(offset=0, size=24)
    assert body["q"] == "Toyota RAV4"
    assert body["Make"] == ["Toyota"]
    assert body["Model"] == ["RAV4"]
    assert body["FuelType"] == ["Hybrid"]
    assert body["AxleConfiguration"] is None
    params = c.build_search_params()
    assert ("q", "Toyota RAV4") in params
    assert ("fuel", "Hybrid") in params
    assert ("axle", "4X4") not in params


def test_fortuner_collector_urls_unchanged():
    settings = Settings(preferred_province="Western Cape", max_mileage_km=100_000)
    at = AutoTraderCollector(settings=settings)
    assert at.search_base_url().endswith("/western-cape/p-9/toyota/fortuner")
    assert at.build_search_params()["transmissiondrive"] == "4x4"
    assert "fueltype" not in at.build_search_params()

    cars = CarsCoZaCollector(settings=settings)
    assert cars.build_search_params()["make_model_variant"] == "Toyota[Fortuner]"
    assert cars.build_search_params()["vehicle_axle_config"] == "4X4"
    assert "vfs_fuel_type" not in cars.build_search_params()

    wbc = WeBuyCarsCollector(settings=settings)
    body = wbc.build_api_body(offset=0, size=24)
    assert body["q"] == "Toyota Fortuner"
    assert body["Model"] == ["Fortuner"]
    assert body["AxleConfiguration"] == ["4X4"]
    assert body["FuelType"] is None


def test_hunt_switch_post(client, db_session, auth):
    get_or_create_active_profile(db_session)
    response = client.post(
        "/hunt",
        data={"hunt_key": "rav4-hybrid", "next": "/"},
        auth=auth,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    active = get_or_create_active_profile(db_session)
    assert active.hunt_key == "rav4-hybrid"

    home = client.get("/", auth=auth)
    assert home.status_code == 200
    assert b"RAV4 Hybrid" in home.content
    assert b"RAV4 Hybrid watch" in home.content


def test_rav4_stock_caption_skips_fortuner_facelift():
    shot = model_shot(CanonicalVehicle(year=2021, model="RAV4", colour="Glacier White"))
    assert "Stock RAV4" in shot["caption"]
    assert "facelift" not in shot["caption"]
    fortuner = model_shot(CanonicalVehicle(year=2021, model="Fortuner", colour="Oxide Bronze"))
    assert "Stock Fortuner" in fortuner["caption"]
    assert "facelift" in fortuner["caption"]
