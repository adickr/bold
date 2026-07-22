"""Deal score and dealer motivation score."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import Settings, get_settings
from app.models.entities import CanonicalVehicle, MotivationLevel


def _days_between(start: datetime | None, end: datetime | None = None) -> int:
    if not start:
        return 0
    end = end or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, (end - start).days)


def compute_deal_score(
    vehicle: CanonicalVehicle,
    *,
    comparable_median: int | None = None,
    settings: Settings | None = None,
) -> tuple[float, dict[str, Any]]:
    settings = settings or get_settings()
    breakdown: dict[str, Any] = {}

    # Price value (30) — below comparable median scores higher
    price = vehicle.current_lowest_price
    median = comparable_median or vehicle.comparable_stats and vehicle.comparable_stats.get(
        "median_price"
    )
    if price and median:
        diff_pct = (median - price) / median
        price_score = max(0.0, min(settings.score_weight_price, settings.score_weight_price * (0.5 + diff_pct)))
    elif price and price <= settings.max_price_zar:
        price_score = settings.score_weight_price * 0.7
    else:
        price_score = settings.score_weight_price * 0.4
    breakdown["price_value"] = round(price_score, 1)

    # Completeness (20) — trim-neutral. VX / GR-S do not score higher.
    # Points reflect known facts (year, mileage, dealer), not desirability.
    known = 0
    if vehicle.year:
        known += 1
    if vehicle.current_mileage_km is not None:
        known += 1
    if vehicle.current_lowest_price is not None:
        known += 1
    if vehicle.primary_dealer or vehicle.primary_location:
        known += 1
    if vehicle.engine or vehicle.variant_normalised:
        known += 1
    completeness = known / 5.0
    spec_score = settings.score_weight_spec * (0.55 + 0.45 * completeness)
    breakdown["completeness"] = round(spec_score, 1)

    # Mileage vs age (15)
    mileage = vehicle.current_mileage_km
    year = vehicle.year
    if mileage is not None and year:
        age = max(1, datetime.now(timezone.utc).year - year)
        expected = age * 15_000
        ratio = mileage / expected if expected else 1
        if ratio <= 0.7:
            mileage_score = settings.score_weight_mileage
        elif ratio <= 1.0:
            mileage_score = settings.score_weight_mileage * 0.85
        elif ratio <= 1.3:
            mileage_score = settings.score_weight_mileage * 0.55
        else:
            mileage_score = settings.score_weight_mileage * 0.3
    else:
        # Missing mileage is a hard trust gap — demote heavily vs known km
        mileage_score = settings.score_weight_mileage * 0.1
    breakdown["mileage"] = round(mileage_score, 1)

    # Reduction history (15)
    total_red = vehicle.total_reduction_zar or 0
    if total_red >= 40_000:
        red_score = settings.score_weight_reduction
    elif total_red >= 20_000:
        red_score = settings.score_weight_reduction * 0.85
    elif total_red >= 10_000:
        red_score = settings.score_weight_reduction * 0.65
    elif total_red > 0:
        red_score = settings.score_weight_reduction * 0.4
    else:
        red_score = settings.score_weight_reduction * 0.25
    breakdown["reduction_history"] = round(red_score, 1)

    # Time on market (10) — sweet spot ~14-45 days
    days = vehicle.days_tracked or _days_between(vehicle.first_seen_at)
    if 14 <= days <= 45:
        time_score = settings.score_weight_time
    elif 7 <= days < 14 or 45 < days <= 75:
        time_score = settings.score_weight_time * 0.75
    elif days < 7:
        time_score = settings.score_weight_time * 0.55
    else:
        time_score = settings.score_weight_time * 0.4
    breakdown["time_on_market"] = round(time_score, 1)

    # History quality (10)
    risks = set(vehicle.risk_flags or [])
    hist = settings.score_weight_history
    if "missing_service_history" in risks:
        hist *= 0.4
    if "accident_or_rebuilt" in risks:
        hist *= 0.2
    if "missing_dealer_info" in risks:
        hist *= 0.7
    if not risks:
        hist *= 0.9  # unknown quality
    breakdown["history_quality"] = round(hist, 1)

    # Risk penalty
    penalty = 0.0
    for flag in risks:
        if flag in {"accident_or_rebuilt", "engine_modification"}:
            penalty += 8
        elif flag in {"4x2_drivetrain", "suspension_modified"}:
            penalty += 10
        elif flag in {"missing_service_history", "stock_photographs"}:
            penalty += 3
        else:
            penalty += 1
    penalty = min(25.0, penalty)
    breakdown["risk_penalty"] = -penalty

    total = (
        breakdown["price_value"]
        + breakdown["completeness"]
        + breakdown["mileage"]
        + breakdown["reduction_history"]
        + breakdown["time_on_market"]
        + breakdown["history_quality"]
        + breakdown["risk_penalty"]
    )
    total = max(0.0, min(100.0, round(total, 1)))
    breakdown["total"] = total
    breakdown["inferred"] = True
    breakdown["note"] = (
        "Score is inferred from asking-price listings, not confirmed sales. "
        "Trim (VX / GR-S / etc.) does not boost the score."
    )
    return total, breakdown


def compute_motivation_score(
    vehicle: CanonicalVehicle,
    *,
    dealer_similar_count: int = 0,
    settings: Settings | None = None,
) -> tuple[float, str, dict[str, Any]]:
    """Estimate negotiation openness. Presented as estimate, not fact."""
    settings = settings or get_settings()
    score = 0.0
    reasons: list[str] = []

    days = vehicle.days_tracked or _days_between(vehicle.first_seen_at)
    if days >= 60:
        score += 25
        reasons.append("long_days_on_market")
    elif days >= 30:
        score += 15
        reasons.append("moderate_days_on_market")
    elif days >= 14:
        score += 8

    red = vehicle.total_reduction_zar or 0
    if red >= 30_000:
        score += 25
        reasons.append("large_total_reduction")
    elif red >= 15_000:
        score += 15
        reasons.append("meaningful_reduction")
    elif red > 0:
        score += 8

    if vehicle.last_reduction_at:
        since = _days_between(vehicle.last_reduction_at)
        if since <= 7:
            score += 10
            reasons.append("recent_reduction")
        elif since >= 30 and days >= 30:
            score += 8
            reasons.append("stale_after_reduction")

    if (vehicle.source_count or 1) >= 3:
        score += 12
        reasons.append("listed_on_many_marketplaces")
    elif (vehicle.source_count or 1) >= 2:
        score += 6

    if dealer_similar_count >= 3:
        score += 10
        reasons.append("dealer_has_similar_stock")

    now = datetime.now(timezone.utc)
    if now.day >= 25:
        score += 5
        reasons.append("end_of_month_timing")

    # Language cues from notes / risk flags aren't always available; check risks for clearance phrasing via flags
    if vehicle.risk_flags and "price_reduced_language" in vehicle.risk_flags:
        score += 8

    score = max(0.0, min(100.0, score))
    if score >= 70:
        level = MotivationLevel.VERY_HIGH.value
    elif score >= 50:
        level = MotivationLevel.HIGH.value
    elif score >= 30:
        level = MotivationLevel.MODERATE.value
    else:
        level = MotivationLevel.LOW.value

    breakdown = {
        "score": score,
        "level": level,
        "reasons": reasons,
        "estimate_only": True,
        "note": "Dealer motivation is an estimate based on listing behaviour, not a confirmed willingness to negotiate.",
    }
    return score, level, breakdown
