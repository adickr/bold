"""Weighted duplicate matching across source listings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.config import Settings, get_settings
from app.models.entities import MatchConfidence, SourceListing
from app.services.normalise import detect_drivetrain, normalise_colour


@dataclass
class MatchResult:
    score: float
    confidence: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _norm_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits or None


def _norm_str(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.lower().split())


def _norm_dealer(value: str | None) -> str | None:
    """Collapse dealer names so slight legal/punctuation variants still match."""
    s = _norm_str(value)
    if not s:
        return None
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(
        r"\b(pty|ltd|limited|cc|inc|incorporated|t\/?a|trading as)\b",
        " ",
        s,
    )
    return " ".join(s.split()) or None


def _norm_drivetrain(value: str | None) -> str | None:
    if not value:
        return None
    detected = detect_drivetrain(value)
    if detected:
        return detected
    compact = re.sub(r"[\s_\-]+", "", value.strip().lower())
    if compact in {"4x4", "4wd", "awd"}:
        return "4x4"
    if compact in {"4x2", "2wd"}:
        return "4x2"
    return compact or None


def _phash_similarity(a: list[Any] | None, b: list[Any] | None) -> float | None:
    if not a or not b:
        return None
    set_a, set_b = set(map(str, a)), set(map(str, b))
    if not set_a or not set_b:
        return None
    overlap = len(set_a & set_b) / max(len(set_a | set_b), 1)
    return overlap


def strong_identity_match(a: SourceListing, b: SourceListing) -> bool:
    """True when VIN / reg / dealer stock number proves the same physical car."""
    if a.vin and b.vin and a.vin.upper() == b.vin.upper():
        return True
    if a.registration and b.registration and a.registration.upper() == b.registration.upper():
        return True
    if (
        a.dealer_stock_number
        and b.dealer_stock_number
        and a.dealer_stock_number.upper() == b.dealer_stock_number.upper()
    ):
        return True
    return False


def score_pair(
    a: SourceListing, b: SourceListing, settings: Settings | None = None
) -> MatchResult:
    settings = settings or get_settings()
    score = 0.0
    evidence: dict[str, Any] = {"signals": []}

    def add(points: float, signal: str) -> None:
        nonlocal score
        score += points
        evidence["signals"].append({"signal": signal, "points": points})

    # Marketplace listing IDs are the identity for that site. Soft signals
    # (dealer + year + stock photos) must never glue different AutoTrader
    # (or Cars.co.za) ads into one canonical vehicle.
    if (
        a.source
        and b.source
        and a.source == b.source
        and a.source_listing_id
        and b.source_listing_id
        and a.source_listing_id != b.source_listing_id
        and not strong_identity_match(a, b)
    ):
        evidence["signals"].append({"signal": "same_source_different_id", "points": 0})
        evidence["score"] = 0.0
        evidence["confidence"] = MatchConfidence.SEPARATE.value
        return MatchResult(
            score=0.0,
            confidence=MatchConfidence.SEPARATE.value,
            evidence=evidence,
        )

    if a.vin and b.vin and a.vin.upper() == b.vin.upper():
        add(100, "same_vin")
    if a.registration and b.registration and a.registration.upper() == b.registration.upper():
        add(100, "same_registration")
    if (
        a.dealer_stock_number
        and b.dealer_stock_number
        and a.dealer_stock_number.upper() == b.dealer_stock_number.upper()
    ):
        add(95, "same_stock_number")

    img_sim = _phash_similarity(a.image_phashes, b.image_phashes)
    if img_sim is not None and img_sim >= 0.7:
        # Cap so image similarity alone cannot auto-merge
        add(min(40.0, 50 * img_sim), "near_identical_images")
    elif a.image_urls and b.image_urls:
        url_overlap = len(set(a.image_urls) & set(b.image_urls))
        # Stock / CDN reuse is common across different Fortuners — keep weak
        if url_overlap >= 2:
            add(30, "identical_image_urls")
        elif url_overlap == 1:
            add(15, "shared_image_url")

    dealer_a, dealer_b = _norm_dealer(a.dealer_name), _norm_dealer(b.dealer_name)
    if dealer_a and dealer_b and dealer_a == dealer_b:
        if a.mileage_km is not None and a.mileage_km == b.mileage_km:
            add(65, "same_dealer_exact_mileage")
        else:
            add(20, "same_dealer")
    elif dealer_a and dealer_b and dealer_a != dealer_b:
        add(-20, "different_dealer")

    if a.year and b.year and a.year == b.year:
        if a.variant_normalised and b.variant_normalised:
            if fuzz.token_set_ratio(a.variant_normalised, b.variant_normalised) >= 85:
                add(25, "same_year_and_variant")
            else:
                add(10, "same_year")
        else:
            add(10, "same_year")

    colour_a = normalise_colour(a.colour) or _norm_str(a.colour)
    colour_b = normalise_colour(b.colour) or _norm_str(b.colour)
    if colour_a and colour_b:
        if colour_a == colour_b:
            add(10, "same_colour")
        else:
            add(-30, "conflicting_colour")

    if a.mileage_km is not None and b.mileage_km is not None:
        delta = abs(a.mileage_km - b.mileage_km)
        if delta <= 100:
            add(15, "mileage_within_100")
        elif delta <= 1000:
            add(5, "mileage_within_1000")

    if a.price_zar is not None and b.price_zar is not None:
        if abs(a.price_zar - b.price_zar) <= 10_000:
            add(10, "price_within_10000")

    phone_a, phone_b = _norm_phone(a.dealer_phone), _norm_phone(b.dealer_phone)
    if phone_a and phone_b and phone_a == phone_b:
        add(15, "same_contact_number")

    dt_a, dt_b = _norm_drivetrain(a.drivetrain), _norm_drivetrain(b.drivetrain)
    if dt_a and dt_b and dt_a != dt_b:
        add(-100, "conflicting_drivetrain")

    if a.description and b.description:
        ratio = fuzz.token_set_ratio(a.description[:500], b.description[:500])
        if ratio >= 90:
            add(15, "similar_description")

    # Clamp
    score = max(0.0, min(score, 100.0))
    if score >= settings.dedup_certain:
        confidence = MatchConfidence.CERTAIN.value
    elif score >= settings.dedup_probable:
        confidence = MatchConfidence.PROBABLE.value
    elif score >= settings.dedup_possible:
        confidence = MatchConfidence.POSSIBLE.value
    else:
        confidence = MatchConfidence.SEPARATE.value

    evidence["score"] = score
    evidence["confidence"] = confidence
    return MatchResult(score=score, confidence=confidence, evidence=evidence)


def should_auto_merge(result: MatchResult, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return result.score >= settings.dedup_probable
