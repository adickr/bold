"""Named hunts: Fortuner 4x4 vs RAV4 Hybrid, etc."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_HUNT_KEY = "fortuner-4x4"

HUNTS: dict[str, dict[str, Any]] = {
    "fortuner-4x4": {
        "key": "fortuner-4x4",
        "name": "Fortuner 4x4",
        "make": "Toyota",
        "model": "Fortuner",
        "required_drivetrain": "4x4",
        "required_fuel": "",
        "max_mileage_km": 100_000,
        "province": "Western Cape",
    },
    "rav4-hybrid": {
        "key": "rav4-hybrid",
        "name": "RAV4 Hybrid",
        "make": "Toyota",
        "model": "RAV4",
        "required_drivetrain": "",
        "required_fuel": "hybrid",
        "max_mileage_km": 100_000,
        "province": "Western Cape",
    },
}


def hunt_preset(key: str | None) -> dict[str, Any]:
    return dict(HUNTS.get(key or "", HUNTS[DEFAULT_HUNT_KEY]))


def hunt_keys() -> list[str]:
    return list(HUNTS.keys())


def model_slug(model: str | None) -> str:
    raw = (model or "Fortuner").strip().lower()
    raw = raw.replace("rav 4", "rav4").replace("rav-4", "rav4")
    return re.sub(r"[^a-z0-9]", "", raw) or "fortuner"


def model_pattern(model: str | None) -> re.Pattern[str]:
    slug = model_slug(model)
    if slug == "rav4":
        return re.compile(r"rav[\s\-]?4", re.I)
    return re.compile(re.escape(model or "Fortuner"), re.I)


def looks_like_model(*parts: str | None, model: str | None = None) -> bool:
    blob = " ".join(p or "" for p in parts)
    if not blob.strip():
        return False
    return bool(model_pattern(model).search(blob))


def is_hybrid_text(*parts: str | None) -> bool:
    blob = " ".join(p or "" for p in parts).lower()
    return bool(
        re.search(
            r"\bhybrid\b|\bhev\b|\bphev\b|plug[\s-]?in|petrol[\s-]?electric|\be-four\b|\bmhev\b",
            blob,
        )
    )


def listing_matches_fuel(*parts: str | None, required_fuel: str | None) -> bool:
    req = (required_fuel or "").strip().lower()
    if not req:
        return True
    if req in {"hybrid", "hev"}:
        return is_hybrid_text(*parts)
    blob = " ".join(p or "" for p in parts).lower()
    return req in blob


def autotrader_model_path(url: str | None) -> tuple[str | None, str | None, str | None]:
    """Return (model_slug, variant_slug, listing_id) from an AutoTrader detail path."""
    if not url:
        return None, None, None
    path = url.split("?", 1)[0]
    match = re.search(
        r"/car-for-sale/toyota/(?P<model>[^/]+)/(?P<slug>[^/]+)/(?P<id>\d{6,})/?$",
        path,
        re.I,
    )
    if not match:
        return None, None, None
    return match.group("model").lower(), match.group("slug"), match.group("id")
