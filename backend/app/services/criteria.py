"""Hard / stretch / negative criteria evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import Settings, get_settings
from app.schemas.listings import ListingPayload, VariantInfo
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

    make_ok = (listing.make or "").lower() == settings.make.lower()
    model_ok = "fortuner" in (listing.model or listing.title or "").lower()
    if not make_ok or not model_ok:
        return CriteriaResult(False, reasons=["not_fortuner"], variant=variant)

    drivetrain = variant.drivetrain or listing.drivetrain
    path = (urlparse(listing.url or "").path or "").lower()
    host = (urlparse(listing.url or "").netloc or "").lower()
    title_blob = " ".join(
        filter(None, [listing.title, listing.variant_raw, listing.description, path])
    )
    # Raised Body = Toyota's 4x2 line (often no "4x2" token in the SEO slug)
    if re.search(r"raised[\s\-]*body", title_blob, re.I):
        drivetrain = "4x2"
    elif re.search(r"(?:^|[-_/])4x2(?:[-_/]|$)", path):
        drivetrain = "4x2"
    elif "autotrader.co.za" in host:
        # AT: only trust 4x4 when the SEO slug says so (chip text lies)
        if re.search(r"(?:^|[-_/])4x4(?:[-_/]|$)", path) or re.search(
            r"(?:^|[-_/])4-x-4(?:[-_/]|$)", path
        ):
            drivetrain = "4x4"
        else:
            drivetrain = None
    elif re.search(r"(?:^|[-_/])4x4(?:[-_/]|$)", path) and drivetrain != "4x2":
        drivetrain = drivetrain or "4x4"

    if drivetrain == "4x2":
        risks.append("4x2_drivetrain")
        return CriteriaResult(False, reasons=["4x2_excluded"], risk_flags=risks, variant=variant)

    # Hard buyer pref: only keep confirmed 4x4 (filter-chip noise must not invent it)
    if drivetrain != "4x4":
        return CriteriaResult(
            False,
            reasons=["drivetrain_unclear" if drivetrain is None else "non_4x4"],
            risk_flags=risks + (["drivetrain_unclear"] if drivetrain is None else []),
            variant=variant,
        )

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
