"""Colour-matched stock Fortuner illustration."""

from app.models.entities import CanonicalVehicle
from app.services.stock import model_shot


def test_model_shot_uses_listing_colour():
    vehicle = CanonicalVehicle(year=2021, colour="Oxide Bronze")
    shot = model_shot(vehicle)
    assert shot["known_colour"] is True
    assert shot["paint"].startswith("#")
    assert shot["paint"] != "#c5c8cc"
    assert "Oxide Bronze" in shot["caption"]
    assert "not this listing" in shot["caption"]
    assert "facelift" in shot["caption"]


def test_model_shot_unknown_colour_falls_back_to_silver():
    shot = model_shot(CanonicalVehicle(year=2019, colour=None))
    assert shot["known_colour"] is False
    assert shot["paint"] == "#c5c8cc"
    assert "colour unknown" in shot["caption"]
    assert "not this listing" in shot["caption"]


def test_model_shot_can_be_built_from_raw_fields():
    shot = model_shot(colour="Glacier White (040)", year=2018)
    assert shot["known_colour"] is True
    assert "Glacier White" in shot["caption"]
    assert "pre-facelift" in shot["caption"]
