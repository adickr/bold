"""Colour-matched Fortuner model shots for the details page.

These are catalog illustrations, not photos of the listed vehicle.
"""

from __future__ import annotations

from typing import Any

from app.models.entities import CanonicalVehicle
from app.services.hunt import model_slug
from app.services.normalise import colour_swatch_css, detect_generation, normalise_colour

_DEFAULT_PAINT = "#c5c8cc"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"


def _shade(paint: str, factor: float) -> str:
    r, g, b = _hex_to_rgb(paint)
    if factor < 1:
        return _rgb_to_hex(int(r * factor), int(g * factor), int(b * factor))
    lift = factor - 1
    return _rgb_to_hex(
        int(r + (255 - r) * lift),
        int(g + (255 - g) * lift),
        int(b + (255 - b) * lift),
    )


def _colour_label(colour: str | None) -> str | None:
    if not colour:
        return None
    cleaned = " ".join(str(colour).split())
    cleaned = cleaned.split("(")[0].strip()
    return cleaned.title() if cleaned else None


def model_shot(vehicle: CanonicalVehicle | None = None, *, colour: str | None = None, year: int | None = None) -> dict[str, Any]:
    """Return paint + caption for a stock Fortuner illustration."""
    colour = colour if colour is not None else (getattr(vehicle, "colour", None) if vehicle else None)
    year = year if year is not None else (getattr(vehicle, "year", None) if vehicle else None)
    paint = colour_swatch_css(colour) or _DEFAULT_PAINT
    label = _colour_label(colour)
    family = normalise_colour(colour)
    generation = detect_generation(year, "")
    known = bool(colour_swatch_css(colour))
    model_name = (getattr(vehicle, "model", None) if vehicle else None) or "Fortuner"
    parts = [f"Stock {model_name}"]
    if label:
        parts.append(label)
    elif not known:
        parts.append("colour unknown")
    if model_slug(model_name) == "fortuner":
        if generation and "pre-facelift" in generation:
            parts.append("pre-facelift shape")
        elif generation and "facelift" in generation:
            parts.append("facelift shape")
    caption = " · ".join(parts) + " · not this listing"
    return {
        "paint": paint,
        "paint_shadow": _shade(paint, 0.62),
        "paint_highlight": _shade(paint, 1.22),
        "family": family,
        "label": label,
        "generation": generation,
        "known_colour": known,
        "caption": caption,
    }
