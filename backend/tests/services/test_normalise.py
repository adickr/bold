"""Variant normalisation tests."""

from app.services.normalise import detect_drivetrain, normalise_variant


def test_normalise_vx_variants():
    samples = [
        "2.8GD-6 4X4 VX",
        "2.8 GD-6 VX 4x4 AT",
        "2.8GD6 4WD VX",
    ]
    for sample in samples:
        info = normalise_variant(title=sample, year=2022)
        assert info.trim == "VX"
        assert info.drivetrain == "4x4"
        assert info.engine and "2.8" in info.engine

    soft = normalise_variant(title="Fortuner VX 2.8 Diesel", year=2022)
    assert soft.trim == "VX"
    assert soft.engine and "2.8" in soft.engine


def test_normalise_grs_aliases():
    for sample in ["GR-S", "GR Sport", "GRS", "2023 Fortuner GR Sport 2.8"]:
        info = normalise_variant(title=sample, year=2023)
        assert info.trim == "GR-S"


def test_reject_4x2_detection():
    assert detect_drivetrain("2021 Fortuner 2.8 4x2 VX") == "4x2"
    assert detect_drivetrain("2.8 GD-6 4x4 VX") == "4x4"
