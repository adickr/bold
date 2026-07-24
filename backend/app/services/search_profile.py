"""Active search profile — editable collect/browse criteria."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.entities import SearchProfile
from app.schemas.listings import VehicleFilterParams


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_drivetrain(value: str | None) -> str:
    raw = (value or "").strip().lower().replace(" ", "")
    if raw in {"", "any", "all", "*"}:
        return ""
    if raw in {"4x4", "4wd", "awd"}:
        return "4x4"
    if raw in {"4x2", "2wd"}:
        return "4x2"
    return raw


def criteria_dict(
    *,
    province: str | None,
    max_mileage_km: int,
    required_drivetrain: str | None,
    max_price_zar: int | None = None,
    enforce_max_price: bool = False,
) -> dict[str, Any]:
    province_n = (province or "").strip() or None
    drivetrain = normalize_drivetrain(required_drivetrain)
    mileage = max(1, int(max_mileage_km or 100_000))
    return {
        "province": province_n,
        "max_mileage_km": mileage,
        "required_drivetrain": drivetrain or None,
        "max_price_zar": int(max_price_zar) if max_price_zar else None,
        "enforce_max_price": bool(enforce_max_price),
    }


def criteria_hash(criteria: dict[str, Any]) -> str:
    payload = {
        "province": criteria.get("province") or None,
        "max_mileage_km": int(criteria.get("max_mileage_km") or 0),
        "required_drivetrain": criteria.get("required_drivetrain") or None,
        "max_price_zar": criteria.get("max_price_zar"),
        "enforce_max_price": bool(criteria.get("enforce_max_price")),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def criteria_label(criteria: dict[str, Any]) -> str:
    parts: list[str] = []
    province = criteria.get("province")
    parts.append(province if province else "Nationwide")
    dt = criteria.get("required_drivetrain")
    parts.append(dt if dt else "any drivetrain")
    mileage = int(criteria.get("max_mileage_km") or 0)
    if mileage:
        if mileage >= 1000:
            parts.append(f"≤{mileage // 1000}k km")
        else:
            parts.append(f"≤{mileage} km")
    if criteria.get("enforce_max_price") and criteria.get("max_price_zar"):
        parts.append(f"≤R{int(criteria['max_price_zar']):,}")
    return " · ".join(parts)


def criteria_from_settings(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return criteria_dict(
        province=settings.preferred_province,
        max_mileage_km=settings.max_mileage_km,
        required_drivetrain=settings.required_drivetrain,
        max_price_zar=settings.max_price_zar,
        enforce_max_price=settings.enforce_max_price,
    )


def profile_to_criteria(profile: SearchProfile) -> dict[str, Any]:
    return criteria_dict(
        province=profile.province,
        max_mileage_km=profile.max_mileage_km,
        required_drivetrain=profile.required_drivetrain,
        max_price_zar=profile.max_price_zar,
        enforce_max_price=profile.enforce_max_price,
    )


def get_or_create_active_profile(db: Session) -> SearchProfile:
    profile = (
        db.execute(
            select(SearchProfile)
            .where(SearchProfile.is_active.is_(True))
            .order_by(SearchProfile.id.asc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if profile:
        return profile

    seed = criteria_from_settings()
    profile = SearchProfile(
        name="Active search",
        is_active=True,
        province=seed.get("province"),
        max_mileage_km=int(seed["max_mileage_km"]),
        required_drivetrain=seed.get("required_drivetrain") or "4x4",
        max_price_zar=seed.get("max_price_zar"),
        enforce_max_price=bool(seed.get("enforce_max_price")),
        criteria_hash=criteria_hash(seed),
        label=criteria_label(seed),
        updated_at=_utcnow(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_active_criteria(db: Session | None = None) -> dict[str, Any]:
    close = False
    if db is None:
        from app.db.session import SessionLocal

        db = SessionLocal()
        close = True
    try:
        profile = get_or_create_active_profile(db)
        return profile_to_criteria(profile)
    finally:
        if close:
            db.close()


def save_active_profile(
    db: Session,
    *,
    province: str | None,
    max_mileage_km: int,
    required_drivetrain: str | None,
    max_price_zar: int | None = None,
    enforce_max_price: bool = False,
) -> SearchProfile:
    criteria = criteria_dict(
        province=province,
        max_mileage_km=max_mileage_km,
        required_drivetrain=required_drivetrain,
        max_price_zar=max_price_zar,
        enforce_max_price=enforce_max_price,
    )
    profile = get_or_create_active_profile(db)
    profile.province = criteria.get("province")
    profile.max_mileage_km = int(criteria["max_mileage_km"])
    profile.required_drivetrain = criteria.get("required_drivetrain")
    profile.max_price_zar = criteria.get("max_price_zar")
    profile.enforce_max_price = bool(criteria.get("enforce_max_price"))
    profile.criteria_hash = criteria_hash(criteria)
    profile.label = criteria_label(criteria)
    profile.updated_at = _utcnow()
    db.commit()
    db.refresh(profile)
    return profile


def settings_for_criteria(criteria: dict[str, Any], base: Settings | None = None) -> Settings:
    """Return a Settings copy with collect/browse overrides from criteria."""
    base = base or get_settings()
    mileage = max(1, int(criteria.get("max_mileage_km") or base.max_mileage_km))
    stretch = max(mileage, int(mileage * 1.1))
    province = criteria.get("province")
    drivetrain = normalize_drivetrain(criteria.get("required_drivetrain"))
    max_price = criteria.get("max_price_zar") or base.max_price_zar
    return base.model_copy(
        update={
            "preferred_province": province or "",
            "max_mileage_km": mileage,
            "stretch_mileage_km": stretch,
            "required_drivetrain": drivetrain or "",
            "max_price_zar": int(max_price),
            "enforce_max_price": bool(criteria.get("enforce_max_price")),
        }
    )


def settings_for_active_search(db: Session | None = None) -> Settings:
    return settings_for_criteria(get_active_criteria(db))


def active_buyer_filters(db: Session | None = None, **overrides: Any) -> VehicleFilterParams:
    """Browse filters aligned with the active search profile."""
    criteria = get_active_criteria(db)
    base = get_settings()
    data: dict[str, Any] = {
        "max_mileage": int(criteria.get("max_mileage_km") or base.max_mileage_km),
        "drivetrain": criteria.get("required_drivetrain") or None,
        "province": criteria.get("province") or None,
        "sort": base.default_sort,
        "active_only": True,
    }
    if criteria.get("enforce_max_price") and criteria.get("max_price_zar"):
        data["max_price"] = int(criteria["max_price_zar"])
    for key, value in overrides.items():
        if value is not None:
            data[key] = value
    return VehicleFilterParams(**data)


def is_hard_reject(reasons: list[str], settings: Settings) -> bool:
    """True when a listing should be deactivated (not merely excluded from this wave)."""
    reason_set = set(reasons or [])
    if "not_fortuner" in reason_set or "invalid_url" in reason_set:
        return True
    req = normalize_drivetrain(settings.required_drivetrain)
    if req == "4x4" and reason_set & {"4x2_excluded", "non_4x4", "drivetrain_unclear"}:
        return True
    if req == "4x2" and reason_set & {"4x4_excluded", "non_4x2", "drivetrain_unclear"}:
        return True
    # Mileage / soft budget failures stay in the DB so changing search later can revive them
    return False
