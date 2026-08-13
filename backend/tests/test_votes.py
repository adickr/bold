"""Thumbs up / down voting for listings."""

from datetime import datetime, timezone

from app.api.queries import sort_vehicles, vote_from_shortlist
from app.models.entities import CanonicalVehicle, ListingStatus, ShortlistEntry, ShortlistStatus, SourceListing
from app.services.shortlist import ShortlistService


def _seed_vehicle(db, *, year=2022, price=600000, score=70.0, listing_id="V1"):
    v = CanonicalVehicle(
        year=year,
        make="Toyota",
        model="Fortuner",
        variant_normalised="2.8 GD-6 4x4",
        drivetrain="4x4",
        current_lowest_price=price,
        current_mileage_km=50000,
        deal_score=score,
        is_active=True,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(v)
    db.flush()
    db.add(
        SourceListing(
            source="autotrader",
            source_listing_id=listing_id,
            url=f"https://www.autotrader.co.za/car-for-sale/toyota/fortuner/{listing_id}",
            year=year,
            price_zar=price,
            drivetrain="4x4",
            listing_status=ListingStatus.ACTIVE.value,
            canonical_vehicle_id=v.id,
        )
    )
    db.commit()
    db.refresh(v)
    return v


def test_set_vote_up_down_clear(db_session):
    v = _seed_vehicle(db_session)
    svc = ShortlistService(db_session)
    up = svc.set_vote(v.id, "up")
    assert up is not None
    assert vote_from_shortlist(up) == "up"
    assert up.status == ShortlistStatus.INTERESTED.value

    down = svc.set_vote(v.id, "down")
    assert vote_from_shortlist(down) == "down"
    assert down.status == ShortlistStatus.REJECTED.value

    cleared = svc.set_vote(v.id, None)
    assert cleared is None
    assert db_session.get(ShortlistEntry, up.id) is None


def test_thumbs_down_sorts_to_bottom(db_session):
    high = _seed_vehicle(db_session, score=90, listing_id="H1", price=500000)
    mid = _seed_vehicle(db_session, score=80, listing_id="M1", price=520000)
    low = _seed_vehicle(db_session, score=60, listing_id="L1", price=540000)
    ShortlistService(db_session).set_vote(high.id, "down")

    ordered = sort_vehicles([high, mid, low], "deal_score_desc")
    assert [v.id for v in ordered] == [mid.id, low.id, high.id]


def test_vote_endpoint_toggles(client, db_session, auth):
    v = _seed_vehicle(db_session, listing_id="API1")
    res = client.post(f"/vehicles/{v.id}/vote", auth=auth, json={"vote": "up"})
    assert res.status_code == 200
    assert res.json()["vote"] == "up"

    # same vote clears
    res2 = client.post(f"/vehicles/{v.id}/vote", auth=auth, json={"vote": "up"})
    assert res2.status_code == 200
    assert res2.json()["vote"] is None

    res3 = client.post(f"/vehicles/{v.id}/vote", auth=auth, json={"vote": "down"})
    assert res3.status_code == 200
    assert res3.json()["vote"] == "down"
