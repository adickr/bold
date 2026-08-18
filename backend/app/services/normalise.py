"""Variant normalisation for Toyota Fortuner listings."""

from __future__ import annotations

import re

from app.schemas.listings import VariantInfo

# Desirability: lower rank = higher preference
TRIM_RANK = {
    "GR-S": 1,
    "GR Sport": 1,
    "VX": 2,
    "Legend": 3,
    "Epic": 3,
    "GD-6": 4,
    "RB": 5,
    "Gazoo": 1,
}

DESIRABLE_COLOURS = {
    "oxide bronze",
    "avant-garde bronze",
    "emotional red",
    "scarlet crystal shine",
    "graphite grey",
    "attitude black",
    "precious metal",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalise_colour(colour: str | None) -> str | None:
    if not colour:
        return None
    c = _clean(colour).lower()
    c = re.sub(r"\([^)]*\)", "", c).strip()
    c = re.sub(r"\s+", " ", c)
    aliases = {
        "pearl white": "white",
        "super white": "white",
        "glacier white": "white",
        "silver metallic": "silver",
        "silver me": "silver",
        "grey metallic": "grey",
        "gray": "grey",
        "graphite grey": "grey",
        "graphite gray": "grey",
        "black mica": "black",
        "attitude black": "attitude black",
        "oxide bronze": "oxide bronze",
        "emotional red": "emotional red",
    }
    return aliases.get(c, c)


# Approximate paint chips for table swatches (asking-listing colour names).
_COLOUR_SWATCHES: dict[str, str] = {
    "white": "#f2f2f0",
    "pearl white": "#f5f4ef",
    "super white": "#f7f7f5",
    "glacier white": "#eef1f4",
    "silver": "#c5c8cc",
    "silver metallic": "#b8bcc2",
    "grey": "#7a7f86",
    "gray": "#7a7f86",
    "graphite grey": "#5c6168",
    "graphite gray": "#5c6168",
    "black": "#1a1c1e",
    "black mica": "#141618",
    "attitude black": "#0f1113",
    "oxide bronze": "#6b4e3a",
    "bronze": "#8a6240",
    "brown": "#5c4033",
    "emotional red": "#9b1c2e",
    "red": "#a31d2a",
    "blue": "#2a4f7a",
    "dark blue": "#1c3558",
    "green": "#2f5a3c",
    "beige": "#cbb89a",
    "gold": "#b8974a",
    "orange": "#c45a1a",
    "yellow": "#d4b84a",
}


def colour_swatch_css(colour: str | None) -> str | None:
    """Return a CSS hex for a listing colour name, if we can map it."""
    if not colour:
        return None
    raw = _clean(colour).lower()
    # Cars.co.za often appends paint codes: "Glacier White (040)"
    raw = re.sub(r"\([^)]*\)", "", raw).strip()
    raw = re.sub(r"\s+", " ", raw)
    if raw in _COLOUR_SWATCHES:
        return _COLOUR_SWATCHES[raw]
    norm = normalise_colour(raw)
    if norm and norm in _COLOUR_SWATCHES:
        return _COLOUR_SWATCHES[norm]
    # Fallback: first token (e.g. "Graphite Grey" → try grey)
    for token in raw.replace("-", " ").split():
        if token in _COLOUR_SWATCHES:
            return _COLOUR_SWATCHES[token]
    return None


"""Drivetrain / engine / trim normalisation helpers."""

def detect_drivetrain(text: str) -> str | None:
    t = text.upper()
    # Toyota "Raised Body" / RB trim is the 4x2 line (SEO: Raised-Body)
    if re.search(r"RAISED[\s\-]*BODY", t):
        return "4x2"
    has_4x2 = bool(re.search(r"\b4X2\b|\b2WD\b|\bRWD\b", t))
    has_4x4 = bool(re.search(r"\b4X4\b|\b4WD\b|\bAWD\b", t))
    if has_4x2 and not has_4x4:
        return "4x2"
    if has_4x4 and not has_4x2:
        return "4x4"
    if has_4x2 and has_4x4:
        # Explicit conflict — prefer the more specific token order in text
        if t.find("4X2") < t.find("4X4") and "4X2" in t:
            return "4x2"
        return "4x4"
    return None


def detect_engine(text: str) -> str | None:
    t = text.upper().replace(" ", "")
    if "2.8" in t or "28GD" in t or "2.8GD" in t or "GD6" in t and "2.8" in text.upper():
        if "GD" in t or "DIESEL" in text.upper():
            return "2.8 GD-6"
        return "2.8"
    if "2.5" in t:
        if re.search(r"HYBRID|\bHEV\b|\bPHEV\b", text.upper()):
            return "2.5 Hybrid"
        return "2.5"
    if "2.4" in t or "24GD" in t:
        return "2.4 GD-6"
    if "4.0" in t or "V6" in t:
        return "4.0 V6"
    if "GD-6" in text.upper() or "GD6" in t:
        return "GD-6"
    return None


def detect_transmission(text: str) -> str | None:
    t = text.upper()
    if re.search(r"\bA/?T\b|\bAUTO(MATIC)?\b|\b6.?SPEED AUTO", t):
        return "automatic"
    if re.search(r"\bM/?T\b|\bMANUAL\b", t):
        return "manual"
    return None


def detect_fuel(text: str) -> str | None:
    t = text.upper()
    if re.search(r"\bHYBRID\b|\bHEV\b|\bPHEV\b|PLUG[\s-]?IN|E-FOUR|PETROL[\s-]?ELECTRIC", t):
        return "hybrid"
    if "DIESEL" in t or "GD" in t:
        return "diesel"
    if "PETROL" in t or "V6" in t:
        return "petrol"
    return None


def detect_trim(text: str) -> tuple[str | None, str | None]:
    t = _clean(text).upper()
    special = None
    trim = None

    if re.search(r"\bGR[-\s]?S\b|\bGR SPORT\b|\bGRS\b|\bGAZOO\b", t):
        trim = "GR-S"
        special = "GR Sport"
    elif re.search(r"\bVX\b", t):
        trim = "VX"
    elif re.search(r"\bADVENTURE\b", t):
        trim = "Adventure"
    elif re.search(r"\bGX-?R\b", t):
        trim = "GX-R"
    elif re.search(r"\bGX\b", t):
        trim = "GX"
    elif re.search(r"\bLEGEND\b", t):
        trim = "Legend"
    elif re.search(r"\bEPIC\b", t):
        trim = "Epic"
    elif re.search(r"\bRB\b|RAISED[\s\-]*BODY", t):
        trim = "RB"
    elif re.search(r"\bGD-?6\b", t):
        trim = "GD-6"

    return trim, special


def detect_generation(year: int | None, text: str) -> str | None:
    if year is None:
        return None
    if year >= 2021:
        return "AN160 facelift"
    if year >= 2016:
        return "AN160 pre-facelift"
    if year >= 2005:
        return "AN50/AN60"
    return None


def desirability_rank(trim: str | None, special: str | None, colour: str | None) -> int:
    rank = TRIM_RANK.get(trim or "", 50)
    if special and "GR" in special.upper():
        rank = min(rank, 1)
    if colour and colour.lower() in DESIRABLE_COLOURS:
        rank = max(1, rank - 1)
    return rank


def normalise_variant(
    title: str | None = None,
    variant_raw: str | None = None,
    description: str | None = None,
    year: int | None = None,
    colour: str | None = None,
    drivetrain_hint: str | None = None,
    transmission_hint: str | None = None,
    fuel_hint: str | None = None,
) -> VariantInfo:
    """Build a controlled taxonomy entry from free-text listing fields."""
    blob = " ".join(filter(None, [title, variant_raw, description]))
    engine = detect_engine(blob)
    transmission = transmission_hint or detect_transmission(blob) or "automatic"
    detected_dt = detect_drivetrain(blob)
    # Explicit 4x2 signals (Raised Body, 4x2) always beat a wrong 4x4 search-scope hint
    if detected_dt == "4x2":
        drivetrain = "4x2"
    else:
        drivetrain = drivetrain_hint or detected_dt
    fuel = fuel_hint or detect_fuel(blob) or ("diesel" if engine and "GD" in engine else None)
    trim, special = detect_trim(blob)
    generation = detect_generation(year, blob)
    colour_n = normalise_colour(colour)

    parts = []
    if engine:
        parts.append(engine)
    if trim and trim not in (engine or ""):
        parts.append(trim)
    if drivetrain:
        parts.append(drivetrain)
    if transmission == "manual":
        parts.append("MT")
    elif transmission == "automatic" and trim in (None, "GD-6", "RB"):
        parts.append("AT")

    variant_normalised = " ".join(parts) if parts else _clean(variant_raw or title or "Fortuner")

    return VariantInfo(
        variant_normalised=variant_normalised,
        engine=engine,
        transmission=transmission,
        drivetrain=drivetrain,
        fuel_type=fuel,
        trim=trim,
        special_edition=special,
        generation=generation,
        desirability_rank=desirability_rank(trim, special, colour_n),
    )


def is_preferred_high_spec(variant: VariantInfo) -> bool:
    return (variant.trim or "") in {"GR-S", "VX"} or (
        variant.special_edition or ""
    ).upper().startswith("GR")
