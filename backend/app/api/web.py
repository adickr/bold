"""Server-rendered dashboard pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.auth import (
    _valid_credentials,
    login_user,
    logout_user,
    require_web_user,
)
from app.api.queries import dashboard_stats, filter_vehicles, vehicle_to_dict
from app.db.session import get_db
from app.models.entities import CanonicalVehicle, ShortlistEntry
from app.schemas.listings import (
    MessageDraftRequest,
    ShortlistCreate,
    ShortlistUpdate,
    VehicleFilterParams,
)
from app.services.shortlist import ShortlistService, draft_dealer_message

from app.workers.scheduler import run_all_collectors


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
router = APIRouter()


def _fmt_zar(value: int | None) -> str:
    if value is None:
        return "—"
    return f"R{value:,}"


templates.env.filters["zar"] = _fmt_zar


@router.post("/collect")
def page_collect(user: str = Depends(require_web_user)):
    """Trigger a live collection run from the dashboard."""
    import os

    from app.config import get_settings

    os.environ["COLLECTOR_MODE"] = "live"
    os.environ["USE_PLAYWRIGHT"] = "true"
    get_settings.cache_clear()
    results = run_all_collectors()
    ok = sum(1 for r in results if r.get("success"))
    return RedirectResponse(f"/?collected={ok}&total={len(results)}", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def page_login(request: Request, next: str = "/", error: str | None = None):
    if request.session.get("user"):
        return RedirectResponse(next or "/", status_code=303)
    return templates.TemplateResponse(
        request,
        "pages/login.html",
        {
            "user": None,
            "next_path": next or "/",
            "error": error,
            "page": "login",
        },
    )


@router.post("/login")
def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if not _valid_credentials(username, password):
        return templates.TemplateResponse(
            request,
            "pages/login.html",
            {
                "user": None,
                "next_path": next or "/",
                "error": "Invalid username or password",
                "page": "login",
            },
            status_code=401,
        )
    login_user(request, username)
    target = next if next.startswith("/") else "/"
    return RedirectResponse(target, status_code=303)


@router.get("/logout")
def do_logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def page_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_web_user),
    collected: int | None = None,
    total: int | None = None,
):
    stats = dashboard_stats(db)
    vehicles = filter_vehicles(db, VehicleFilterParams())[:8]
    return templates.TemplateResponse(
        request,
        "pages/dashboard.html",
        {
            "user": user,
            "stats": stats,
            "vehicles": [vehicle_to_dict(v) for v in vehicles],
            "page": "dashboard",
            "collected": collected,
            "total": total,
        },
    )


@router.get("/listings", response_class=HTMLResponse)
def page_listings(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_web_user),
    min_price: int | None = None,
    max_price: int | None = None,
    max_mileage: int | None = None,
    min_year: int | None = None,
    variant: str | None = None,
    province: str | None = None,
    dealer: str | None = None,
    min_deal_score: float | None = None,
    has_reduction: bool | None = None,
    new_only: bool | None = None,
    shortlisted: bool | None = None,
    stretch: bool | None = None,
    q: str | None = None,
):
    params = VehicleFilterParams(
        min_price=min_price,
        max_price=max_price,
        max_mileage=max_mileage,
        min_year=min_year,
        variant=variant,
        province=province,
        dealer=dealer,
        min_deal_score=min_deal_score,
        has_reduction=has_reduction,
        new_only=new_only,
        shortlisted=shortlisted,
        stretch=stretch,
        q=q,
    )
    vehicles = filter_vehicles(db, params)
    return templates.TemplateResponse(
        request,
        "pages/listings.html",
        {
            "user": user,
            "vehicles": [vehicle_to_dict(v) for v in vehicles],
            "filters": params.model_dump(),
            "page": "listings",
        },
    )


@router.get("/vehicles/{vehicle_id}", response_class=HTMLResponse)
def page_vehicle(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_web_user),
):
    vehicle = db.execute(
        select(CanonicalVehicle)
        .where(CanonicalVehicle.id == vehicle_id)
        .options(
            selectinload(CanonicalVehicle.source_listings),
            selectinload(CanonicalVehicle.price_events),
            selectinload(CanonicalVehicle.shortlist_entry),
            selectinload(CanonicalVehicle.match_evidence),
        )
    ).scalar_one_or_none()
    if not vehicle:
        return HTMLResponse("Not found", status_code=404)
    if vehicle.shortlist_entry and vehicle.shortlist_entry.draft_message:
        draft = vehicle.shortlist_entry.draft_message
    else:
        draft = draft_dealer_message(vehicle)
    return templates.TemplateResponse(
        request,
        "pages/vehicle.html",
        {
            "user": user,
            "v": vehicle,
            "summary": vehicle_to_dict(vehicle),
            "draft": draft,
            "page": "listings",
        },
    )


@router.post("/vehicles/{vehicle_id}/shortlist")
def page_add_shortlist(
    vehicle_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_web_user),
    notes: str = Form(""),
    status: str = Form("watching"),
):
    ShortlistService(db).add(
        ShortlistCreate(canonical_vehicle_id=vehicle_id, status=status, notes=notes or None)
    )
    return RedirectResponse(f"/vehicles/{vehicle_id}", status_code=303)


@router.post("/shortlist/{entry_id}/update")
def page_update_shortlist(
    entry_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_web_user),
    status: str = Form(...),
    notes: str = Form(""),
    interest_level: int = Form(3),
    offered_price_zar: int | None = Form(None),
    dealer_response: str = Form(""),
    rejection_reason: str = Form(""),
):
    ShortlistService(db).update(
        entry_id,
        ShortlistUpdate(
            status=status,
            notes=notes or None,
            interest_level=interest_level,
            offered_price_zar=offered_price_zar or None,
            dealer_response=dealer_response or None,
            rejection_reason=rejection_reason or None,
        ),
    )
    entry = db.get(ShortlistEntry, entry_id)
    vid = entry.canonical_vehicle_id if entry else ""
    return RedirectResponse(f"/vehicles/{vid}", status_code=303)


@router.get("/shortlist", response_class=HTMLResponse)
def page_shortlist(
    request: Request, db: Session = Depends(get_db), user: str = Depends(require_web_user)
):
    entries = ShortlistService(db).list_entries()
    return templates.TemplateResponse(
        request,
        "pages/shortlist.html",
        {
            "user": user,
            "entries": entries,
            "page": "shortlist",
        },
    )


@router.post("/vehicles/{vehicle_id}/draft")
def page_refresh_draft(
    vehicle_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_web_user),
    preferred_offer_zar: int | None = Form(None),
):
    vehicle = db.get(CanonicalVehicle, vehicle_id)
    if vehicle:
        msg = draft_dealer_message(
            vehicle, MessageDraftRequest(preferred_offer_zar=preferred_offer_zar)
        )
        if vehicle.shortlist_entry:
            vehicle.shortlist_entry.draft_message = msg
            db.commit()
    return RedirectResponse(f"/vehicles/{vehicle_id}", status_code=303)
