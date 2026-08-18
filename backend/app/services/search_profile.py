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
from app.services.hunt import DEFAULT_HUNT_KEY, HUNTS, hunt_preset


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


def normalize_fuel(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"", "any", "all", "*"}:
        return ""
    if raw in {"hybrid", "hev", "phev"}:
        return "hybrid"
    return raw


def criteria_dict(
    *,
    province: str | None,
    max_mileage_km: int,
    required_drivetrain: str | None,
    max_price_zar: int | None = None,
    enforce_max_price: bool = False,
    hunt_key: str | None = None,
    make: str | None = None,
    model: str | None = None,
    required_fuel: str | None = None,
) -> dict[str, Any]:
    preset = hunt_preset(hunt_key)
    province_n = (province or "").strip() or None
    drivetrain = normalize_drivetrain(required_drivetrain)
    fuel = normalize_fuel(required_fuel if required_fuel is not None else preset.get("required_fuel"))
    mileage = max(1, int(max_mileage_km or 100_000))
    return {
        "hunt_key": preset["key"],
        "hunt_name": preset["name"],
        "make": (make or preset["make"]).strip() or preset["make"],
        "model": (model or preset["model"]).strip() or preset["model"],
        "province": province_n,
        "max_mileage_km": mileage,
        "required_drivetrain": drivetrain or None,
        "required_fuel": fuel or None,
        "max_price_zar": int(max_price_zar) if max_price_zar else None,
        "enforce_max_price": bool(enforce_max_price),
    }


def criteria_hash(criteria: dict[str, Any]) -> str:
    payload = {
        "hunt_key": criteria.get("hunt_key") or DEFAULT_HUNT_KEY,
        "make": criteria.get("make") or "Toyota",
        "model": criteria.get("model") or "Fortuner",
        "province": criteria.get("province") or None,
        "max_mileage_km": int(criteria.get("max_mileage_km") or 0),
        "required_drivetrain": criteria.get("required_drivetrain") or None,
        "required_fuel": criteria.get("required_fuel") or None,
        "max_price_zar": criteria.get("max_price_zar"),
        "enforce_max_price": bool(criteria.get("enforce_max_price")),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def criteria_label(criteria: dict[str, Any]) -> str:
    parts: list[str] = []
    hunt_name = criteria.get("hunt_name") or hunt_preset(criteria.get("hunt_key")).get("name")
    if hunt_name:
        parts.append(str(hunt_name))
    province = criteria.get("province")
    parts.append(province if province else "Nationwide")
    fuel = criteria.get("required_fuel")
    if fuel:
        parts.append(fuel)
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
        hunt_key=DEFAULT_HUNT_KEY,
        make=settings.make,
        model=settings.model,
        required_fuel=settings.required_fuel,
    )


def profile_to_criteria(profile: SearchProfile) -> dict[str, Any]:
    return criteria_dict(
        province=profile.province,
        max_mileage_km=profile.max_mileage_km,
        required_drivetrain=profile.required_drivetrain,
        max_price_zar=profile.max_price_zar,
        enforce_max_price=profile.enforce_max_price,
        hunt_key=getattr(profile, "hunt_key", None) or DEFAULT_HUNT_KEY,
        make=getattr(profile, "make", None),
        model=getattr(profile, "model", None),
        required_fuel=getattr(profile, "required_fuel", None),
    )


def _apply_criteria(profile: SearchProfile, criteria: dict[str, Any]) -> None:
    profile.hunt_key = criteria.get("hunt_key") or DEFAULT_HUNT_KEY
    profile.make = criteria.get("make") or "Toyota"
    profile.model = criteria.get("model") or "Fortuner"
    profile.name = criteria.get("hunt_name") or profile.name or "Active search"
    profile.province = criteria.get("province")
    profile.max_mileage_km = int(criteria["max_mileage_km"])
    profile.required_drivetrain = criteria.get("required_drivetrain")
    profile.required_fuel = criteria.get("required_fuel")
    profile.max_price_zar = criteria.get("max_price_zar")
    profile.enforce_max_price = bool(criteria.get("enforce_max_price"))
    profile.criteria_hash = criteria_hash(criteria)
    profile.label = criteria_label(criteria)
    profile.updated_at = _utcnow()


def _new_profile(criteria: dict[str, Any], *, is_active: bool) -> SearchProfile:
    profile = SearchProfile(is_active=is_active)
    _apply_criteria(profile, criteria)
    return profile


def ensure_hunt_profiles(db: Session) -> SearchProfile:
    """Guarantee both hunts exist; return the active one (Fortuner by default)."""
    rows = list(db.execute(select(SearchProfile).order_by(SearchProfile.id.asc())).scalars())
    by_key: dict[str, SearchProfile] = {}
    for row in rows:
        key = (getattr(row, "hunt_key", None) or "").strip() or DEFAULT_HUNT_KEY
        row.hunt_key = key
        if not getattr(row, "make", None):
            row.make = hunt_preset(key)["make"]
        if not getattr(row, "model", None):
            row.model = hunt_preset(key)["model"]
        by_key.setdefault(key, row)

    for key, preset in HUNTS.items():
        if key in by_key:
            continue
        seed = criteria_dict(
            province=preset.get("province"),
            max_mileage_km=int(preset.get("max_mileage_km") or 100_000),
            required_drivetrain=preset.get("required_drivetrain") or "",
            hunt_key=key,
            make=preset["make"],
            model=preset["model"],
            required_fuel=preset.get("required_fuel") or "",
        )
        profile = _new_profile(seed, is_active=False)
        db.add(profile)
        by_key[key] = profile

    active = next((row for row in rows if row.is_active), None) or by_key.get(DEFAULT_HUNT_KEY)
    if active is None:
        active = next(iter(by_key.values()))
    for row in by_key.values():
        row.is_active = False
    active.is_active = True
    db.commit()
    db.refresh(active)
    return active


def get_or_create_active_profile(db: Session) -> SearchProfile:
    return ensure_hunt_profiles(db)


def list_hunts(db: Session) -> list[dict[str, Any]]:
    ensure_hunt_profiles(db)
    rows = list(db.execute(select(SearchProfile).order_by(SearchProfile.id.asc())).scalars())
    seen: set[str] = set()
    hunts: list[dict[str, Any]] = []
    for row in rows:
        key = row.hunt_key or DEFAULT_HUNT_KEY
        if key in seen:
            continue
        seen.add(key)
        preset = hunt_preset(key)
        hunts.append(
            {
                "key": key,
                "name": preset["name"],
                "active": bool(row.is_active),
                "label": row.label,
            }
        )
    for key, preset in HUNTS.items():
        if key not in seen:
            hunts.append({"key": key, "name": preset["name"], "active": False, "label": None})
    return hunts


def activate_hunt(db: Session, hunt_key: str) -> SearchProfile:
    preset = hunt_preset(hunt_key)
    ensure_hunt_profiles(db)
    rows = list(db.execute(select(SearchProfile)).scalars())
    chosen = next((row for row in rows if (row.hunt_key or DEFAULT_HUNT_KEY) == preset["key"]), None)
    if chosen is None:
        seed = criteria_dict(
            province=preset.get("province"),
            max_mileage_km=int(preset.get("max_mileage_km") or 100_000),
            required_drivetrain=preset.get("required_drivetrain") or "",
            hunt_key=preset["key"],
            make=preset["make"],
            model=preset["model"],
            required_fuel=preset.get("required_fuel") or "",
        )
        chosen = _new_profile(seed, is_active=True)
        db.add(chosen)
        rows.append(chosen)
    for row in rows:
        row.is_active = row is chosen
    db.commit()
    db.refresh(chosen)
    return chosen


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
    profile = get_or_create_active_profile(db)
    criteria = criteria_dict(
        province=province,
        max_mileage_km=max_mileage_km,
        required_drivetrain=required_drivetrain,
        max_price_zar=max_price_zar,
        enforce_max_price=enforce_max_price,
        hunt_key=profile.hunt_key or DEFAULT_HUNT_KEY,
        make=profile.make,
        model=profile.model,
        required_fuel=profile.required_fuel,
    )
    _apply_criteria(profile, criteria)
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
            "required_fuel": normalize_fuel(criteria.get("required_fuel")),
            "make": criteria.get("make") or base.make,
            "model": criteria.get("model") or base.model,
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
        "make": criteria.get("make") or None,
        "model": criteria.get("model") or None,
        "fuel_type": criteria.get("required_fuel") or None,
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
    if reason_set & {"not_fortuner", "wrong_model", "fuel_mismatch", "invalid_url"}:
        return True
    req = normalize_drivetrain(settings.required_drivetrain)
    if req == "4x4" and reason_set & {"4x2_excluded", "non_4x4", "drivetrain_unclear"}:
        return True
    if req == "4x2" and reason_set & {"4x4_excluded", "non_4x2", "drivetrain_unclear"}:
        return True
    # Mileage / soft budget failures stay in the DB so changing search later can revive them
    return False
