"""Colour swatch mapping for table chips."""

from app.services.normalise import colour_swatch_css, normalise_colour


def test_colour_swatch_maps_common_names():
    assert colour_swatch_css("White") == "#f2f2f0"
    assert colour_swatch_css("Oxide Bronze") == "#6b4e3a"
    assert colour_swatch_css("Graphite Grey").startswith("#")
    assert colour_swatch_css("Emotional Red") == "#9b1c2e"
    assert colour_swatch_css(None) is None
    assert colour_swatch_css("Totally Made Up Paint") is None
    assert normalise_colour("Gray") == "grey"
