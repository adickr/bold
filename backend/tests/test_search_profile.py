"""Tests for dynamic search profiles and soft criteria handling."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.session import Base
from app.models.entities import CollectorRun, ListingStatus, SearchProfile, SourceListing
from app.schemas.listings import ListingPayload
from app.services.criteria import evaluate_listing
from app.services.ingestion import IngestionService
from app.services.search_profile import (
    criteria_hash,
    criteria_label,
    get_or_create_active_profile,
    is_hard_reject,
    profile_to_criteria,
    save_active_profile,
    settings_for_criteria,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()


def test_criteria_hash_is_stable():
    a = {
        "province": "Western Cape",
        "max_mileage_km": 100000,
        "required_drivetrain": "4x4",
        "max_price_zar": None,
        "enforce_max_price": False,
    }
    b = dict(a)
    c = {**a, "province": "Gauteng"}
    assert criteria_hash(a) == criteria_hash(b)
    assert criteria_hash(a) != criteria_hash(c)


def test_criteria_label():
    label = criteria_label(
        {
            "province": "Western Cape",
            "max_mileage_km": 80000,
            "required_drivetrain": "4x4",
        }
    )
    assert "Western Cape" in label
    assert "4x4" in label
    assert "≤80k" in label
    any_axle = criteria_label(
        {
            "province": None,
            "max_mileage_km": 120000,
            "required_drivetrain": None,
        }
    )
    assert "Nationwide" in any_axle
    assert "any drivetrain" in any_axle


def test_settings_for_criteria_overrides_defaults():
    settings = settings_for_criteria(
        {
            "province": "Gauteng",
            "max_mileage_km": 80000,
            "required_drivetrain": "4x2",
            "max_price_zar": 700000,
            "enforce_max_price": True,
        }
    )
    assert settings.preferred_province == "Gauteng"
    assert settings.max_mileage_km == 80000
    assert settings.required_drivetrain == "4x2"
    assert settings.enforce_max_price is True


def test_ensure_default_and_save_search_profile():
    db = _session()
    profile = get_or_create_active_profile(db)
    assert profile.is_active is True
    assert profile.province == "Western Cape"

    saved = save_active_profile(
        db,
        province="Gauteng",
        max_mileage_km=90000,
        required_drivetrain="",
        max_price_zar=700000,
        enforce_max_price=False,
    )
    assert saved.province == "Gauteng"
    assert saved.required_drivetrain is None
    assert saved.max_mileage_km == 90000
    assert get_or_create_active_profile(db).id == saved.id
    assert db.scalar(select(SearchProfile).where(SearchProfile.is_active.is_(True))) is not None
    criteria = profile_to_criteria(saved)
    assert criteria["province"] == "Gauteng"
    assert criteria["required_drivetrain"] is None


def test_drivetrain_optional_does_not_reject():
    listing = ListingPayload(
        source="cars_co_za",
        source_listing_id="1",
        url="https://www.cars.co.za/usedcars/toyota/fortuner/123/",
        title="Toyota Fortuner 2.8 GD-6",
        make="Toyota",
        model="Fortuner",
        year=2021,
        price_zar=550000,
        mileage_km=60000,
        dealer_location="Western Cape",
        drivetrain=None,
        transmission="Automatic",
        fuel_type="Diesel",
        dealer_name="Dealer",
    )
    decision = evaluate_listing(
        listing,
        Settings(
            preferred_province="Western Cape",
            max_mileage_km=100000,
            required_drivetrain="",
        ),
    )
    assert decision.accepted is True


def test_mileage_fail_is_soft_not_hard():
    settings = Settings(max_mileage_km=50000, stretch_mileage_km=55000, required_drivetrain="4x4")
    assert is_hard_reject(["mileage_too_high"], settings) is False
    assert is_hard_reject(["4x2_excluded"], settings) is True
    assert is_hard_reject(["not_fortuner"], settings) is True


def test_mark_missing_skips_listings_outside_active_search():
    db = _session()
    settings = Settings(
        preferred_province="Western Cape",
        max_mileage_km=50000,
        stretch_mileage_km=55000,
        required_drivetrain="4x4",
        missed_scans_possibly_removed=1,
        missed_scans_removed=2,
    )
    service = IngestionService(db, settings)
    high_km = SourceListing(
        source="cars_co_za",
        source_listing_id="high-km",
        url="https://www.cars.co.za/usedcars/toyota/fortuner/2020-2-8-gd-6-4x4-vx/high/",
        title="Toyota Fortuner 2.8 GD-6 4x4",
        make="Toyota",
        model="Fortuner",
        year=2020,
        price_zar=500000,
        mileage_km=80000,
        dealer_location="Western Cape",
        drivetrain="4x4",
        variant_raw="2.8 GD-6 4x4",
        transmission="Automatic",
        fuel_type="Diesel",
        dealer_name="Dealer",
        listing_status=ListingStatus.ACTIVE.value,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        consecutive_misses=0,
    )
    in_window = SourceListing(
        source="cars_co_za",
        source_listing_id="in-window",
        url="https://www.cars.co.za/usedcars/toyota/fortuner/2021-2-8-gd-6-4x4-vx/in/",
        title="Toyota Fortuner 2.8 GD-6 4x4",
        make="Toyota",
        model="Fortuner",
        year=2021,
        price_zar=520000,
        mileage_km=30000,
        dealer_location="Western Cape",
        drivetrain="4x4",
        variant_raw="2.8 GD-6 4x4",
        transmission="Automatic",
        fuel_type="Diesel",
        dealer_name="Dealer",
        listing_status=ListingStatus.ACTIVE.value,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        consecutive_misses=0,
    )
    db.add_all([high_km, in_window])
    db.commit()

    service._mark_missing("cars_co_za", seen_ids=set())
    db.flush()
    db.refresh(high_km)
    db.refresh(in_window)
    assert high_km.listing_status == ListingStatus.ACTIVE.value
    assert high_km.consecutive_misses == 0
    assert in_window.consecutive_misses >= 1


def test_search_profile_post_saves_and_redirects(client, db_session, auth):
    response = client.post(
        "/search-profile",
        data={
            "province": "Gauteng",
            "max_mileage_km": "75000",
            "required_drivetrain": "4x2",
            "max_price_zar": "700000",
            "action": "save",
        },
        auth=auth,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/?saved=1"

    profile = get_or_create_active_profile(db_session)
    assert profile.province == "Gauteng"
    assert profile.max_mileage_km == 75000
    assert profile.required_drivetrain == "4x2"
    assert profile.max_price_zar == 700000


def test_collector_run_stores_criteria():
    db = _session()
    criteria = {
        "province": "Gauteng",
        "max_mileage_km": 90000,
        "required_drivetrain": "4x4",
        "max_price_zar": None,
        "enforce_max_price": False,
    }
    run = CollectorRun(
        source="cars_co_za",
        success=True,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        listings_found=1,
        listings_new=1,
        listings_updated=0,
        criteria=criteria,
    )
    db.add(run)
    db.commit()
    loaded = db.get(CollectorRun, run.id)
    assert loaded is not None
    assert loaded.criteria["province"] == "Gauteng"


def test_default_buyer_filters_follow_saved_profile(db_session):
    from app.api.queries import default_buyer_filters

    save_active_profile(
        db_session,
        province="KwaZulu-Natal",
        max_mileage_km=60000,
        required_drivetrain="4x2",
    )
    params = default_buyer_filters(db_session)
    assert params.province == "KwaZulu-Natal"
    assert params.max_mileage == 60000
    assert params.drivetrain == "4x2"
    assert params.model == "Fortuner"
    assert params.fuel_type is None
