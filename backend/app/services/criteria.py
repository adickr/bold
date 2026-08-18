"""Hard / stretch / negative criteria evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import Settings, get_settings
from app.schemas.listings import ListingPayload, VariantInfo
from app.services.hunt import listing_matches_fuel, looks_like_model, model_slug
from app.services.normalise import normalise_variant


NEGATIVE_PATTERNS = [
    (r"accident|rebuilt|write[\s-]?off|salvage", "accident_or_rebuilt"),
    (r"ecu\s*tun|remap|chipped|stage\s*[123]", "engine_modification"),
    (r"lifted|lift\s*kit|air\s*suspension\s*mod", "suspension_modified"),
    (r"no\s*service\s*history|service\s*history\s*missing", "missing_service_history"),
    (r"stock\s*photo|catalogue\s*image", "stock_photographs"),
]


@dataclass
class CriteriaResult:
    accepted: bool
    is_stretch: bool = False
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    variant: VariantInfo | None = None


def evaluate_listing(
    listing: ListingPayload, settings: Settings | None = None
) -> CriteriaResult:
    settings = settings or get_settings()
    variant = normalise_variant(
        title=listing.title,
        variant_raw=listing.variant_raw,
        description=listing.description,
        year=listing.year,
        colour=listing.colour,
        drivetrain_hint=listing.drivetrain,
        transmission_hint=listing.transmission,
        fuel_hint=listing.fuel_type,
    )

    reasons: list[str] = []
    risks: list[str] = []

    make_ok = (listing.make or "").lower() == (settings.make or "Toyota").lower()
    wanted_model = settings.model or "Fortuner"
    model_ok = looks_like_model(
        listing.model, listing.title, listing.variant_raw, listing.url, model=wanted_model
    )
    if not make_ok or not model_ok:
        reason = "not_fortuner" if model_slug(wanted_model) == "fortuner" else "wrong_model"
        return CriteriaResult(False, reasons=[reason], variant=variant)

    if not listing_matches_fuel(
        listing.fuel_type,
        listing.title,
        listing.variant_raw,
        listing.description,
        listing.url,
        required_fuel=settings.required_fuel,
    ):
        return CriteriaResult(False, reasons=["fuel_mismatch"], variant=variant)

    drivetrain = variant.drivetrain or listing.drivetrain
    path = (urlparse(listing.url or "").path or "").lower()
    title_blob = " ".join(
        filter(None, [listing.title, listing.variant_raw, listing.description, path])
    )
    # Raised Body = Toyota's 4x2 line (often no "4x2" token in the SEO slug)
    if re.search(r"raised[\s\-]*body", title_blob, re.I):
        drivetrain = "4x2"
    elif re.search(r"(?:^|[-_/])4x2(?:[-_/]|$)", path):
        drivetrain = "4x2"
    elif re.search(r"(?:^|[-_/])4x4(?:[-_/]|$)", path) and drivetrain != "4x2":
        drivetrain = drivetrain or "4x4"

    if drivetrain == "4x2":
        risks.append("4x2_drivetrain")
        # Only hard-exclude 4x2 when the active search requires 4x4
        req = (settings.required_drivetrain or "").strip().lower().replace(" ", "")
        if req in {"4x4", "4wd", "awd"}:
            return CriteriaResult(False, reasons=["4x2_excluded"], risk_flags=risks, variant=variant)
        if req in {"4x2", "2wd"}:
            pass  # accepted below
        # any / empty: keep going

    req = (settings.required_drivetrain or "").strip().lower().replace(" ", "")
    if req in {"4x4", "4wd", "awd"}:
        # Hard buyer pref: only keep confirmed 4x4 (filter-chip noise must not invent it)
        if drivetrain != "4x4":
            return CriteriaResult(
                False,
                reasons=["drivetrain_unclear" if drivetrain is None else "non_4x4"],
                risk_flags=risks + (["drivetrain_unclear"] if drivetrain is None else []),
                variant=variant,
            )
    elif req in {"4x2", "2wd"}:
        if drivetrain == "4x4":
            return CriteriaResult(False, reasons=["4x4_excluded"], risk_flags=risks, variant=variant)
        if drivetrain != "4x2":
            return CriteriaResult(
                False,
                reasons=["drivetrain_unclear" if drivetrain is None else "non_4x2"],
                risk_flags=risks + (["drivetrain_unclear"] if drivetrain is None else []),
                variant=variant,
            )
    # else: any drivetrain accepted

    price = listing.price_zar
    mileage = listing.mileage_km
    is_stretch = False

    if price is None:
        risks.append("missing_price")
    elif price > settings.max_price_zar:
        # Price is not a hard reject — prefer cheaper via sort/score, but flag above comfort budget
        is_stretch = True
        reasons.append("above_comfort_price")

    if mileage is None:
        risks.append("missing_mileage")
    elif mileage > settings.stretch_mileage_km:
        return CriteriaResult(False, reasons=["mileage_too_high"], variant=variant)
    elif mileage > settings.max_mileage_km:
        is_stretch = True
        reasons.append("stretch_mileage")

    # Stretch is price/mileage only — VX / GR-S do not justify over-budget
    blob = " ".join(filter(None, [listing.title, listing.description, listing.variant_raw]))
    for pattern, flag in NEGATIVE_PATTERNS:
        if re.search(pattern, blob, re.I):
            risks.append(flag)

    if not listing.dealer_name and not listing.dealer_phone:
        risks.append("missing_dealer_info")

    return CriteriaResult(
        accepted=True,
        is_stretch=is_stretch,
        reasons=reasons,
        risk_flags=risks,
        variant=variant,
    )
