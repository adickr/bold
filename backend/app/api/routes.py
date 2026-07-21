"""JSON API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.api.auth import require_user
from app.api.queries import dashboard_stats, filter_vehicles, vehicle_to_dict
from app.db.session import get_db
from app.models.entities import CanonicalVehicle, CollectorRun
from app.schemas.listings import (
    MergeRequest,
    MessageDraftRequest,
    ShortlistCreate,
    ShortlistUpdate,
    UnmergeRequest,
    VehicleFilterParams,
)
from app.services.ingestion import IngestionService
from app.services.shortlist import ShortlistService, draft_dealer_message
from app.workers.scheduler import run_all_collectors, run_collector, send_daily_digest_job

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/dashboard")
def api_dashboard(db: Session = Depends(get_db)):
    return dashboard_stats(db)


@router.get("/vehicles")
def api_vehicles(
    min_price: int | None = None,
    max_price: int | None = None,
    min_mileage: int | None = None,
    max_mileage: int | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    variant: str | None = None,
    province: str | None = None,
    dealer: str | None = None,
    min_deal_score: float | None = None,
    min_days: int | None = None,
    max_days: int | None = None,
    has_reduction: bool | None = None,
    new_only: bool | None = None,
    shortlisted: bool | None = None,
    stretch: bool | None = None,
    active_only: bool = True,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    params = VehicleFilterParams(
        min_price=min_price,
        max_price=max_price,
        min_mileage=min_mileage,
        max_mileage=max_mileage,
        min_year=min_year,
        max_year=max_year,
        variant=variant,
        province=province,
        dealer=dealer,
        min_deal_score=min_deal_score,
        min_days=min_days,
        max_days=max_days,
        has_reduction=has_reduction,
        new_only=new_only,
        shortlisted=shortlisted,
        stretch=stretch,
        active_only=active_only,
        q=q,
    )
    vehicles = filter_vehicles(db, params)
    return [vehicle_to_dict(v) for v in vehicles]


@router.get("/vehicles/{vehicle_id}")
def api_vehicle_detail(vehicle_id: int, db: Session = Depends(get_db)):
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
        raise HTTPException(404, "Vehicle not found")
    data = vehicle_to_dict(vehicle)
    data.update(
        {
            "description_sources": [
                {
                    "id": s.id,
                    "source": s.source,
                    "url": s.url,
                    "title": s.title,
                    "price": s.price_zar,
                    "mileage": s.mileage_km,
                    "status": s.listing_status,
                    "dealer": s.dealer_name,
                    "variant_raw": s.variant_raw,
                    "first_seen_at": s.first_seen_at,
                    "last_seen_at": s.last_seen_at,
                }
                for s in vehicle.source_listings
            ],
            "price_history": [
                {
                    "at": e.observed_at,
                    "old": e.old_price_zar,
                    "new": e.new_price_zar,
                    "change": e.change_zar,
                    "source": e.source,
                    "note": e.note,
                }
                for e in sorted(vehicle.price_events, key=lambda x: x.observed_at or 0)
            ],
            "deal_score_breakdown": vehicle.deal_score_breakdown,
            "motivation_breakdown": vehicle.motivation_breakdown,
            "comparable_stats": vehicle.comparable_stats,
            "match_evidence": [
                {
                    "score": m.score,
                    "confidence": m.confidence,
                    "evidence": m.evidence,
                    "manual": m.is_manual,
                    "listing_a_id": m.listing_a_id,
                    "listing_b_id": m.listing_b_id,
                }
                for m in vehicle.match_evidence
            ],
            "shortlist": None
            if not vehicle.shortlist_entry
            else {
                "id": vehicle.shortlist_entry.id,
                "status": vehicle.shortlist_entry.status,
                "interest_level": vehicle.shortlist_entry.interest_level,
                "notes": vehicle.shortlist_entry.notes,
                "dealer_contacted": vehicle.shortlist_entry.dealer_contacted,
                "dealer_response": vehicle.shortlist_entry.dealer_response,
                "offered_price_zar": vehicle.shortlist_entry.offered_price_zar,
                "counteroffer_zar": vehicle.shortlist_entry.counteroffer_zar,
                "viewing_date": vehicle.shortlist_entry.viewing_date,
                "rejection_reason": vehicle.shortlist_entry.rejection_reason,
                "draft_message": vehicle.shortlist_entry.draft_message,
            },
            "manual_notes": vehicle.manual_notes,
        }
    )
    return data


@router.post("/shortlist")
def api_shortlist_add(data: ShortlistCreate, db: Session = Depends(get_db)):
    entry = ShortlistService(db).add(data)
    return {"id": entry.id, "status": entry.status}


@router.patch("/shortlist/{entry_id}")
def api_shortlist_update(entry_id: int, data: ShortlistUpdate, db: Session = Depends(get_db)):
    try:
        entry = ShortlistService(db).update(entry_id, data)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": entry.id, "status": entry.status}


@router.get("/shortlist")
def api_shortlist_list(db: Session = Depends(get_db)):
    entries = ShortlistService(db).list_entries()
    return [
        {
            "id": e.id,
            "status": e.status,
            "interest_level": e.interest_level,
            "notes": e.notes,
            "vehicle": vehicle_to_dict(e.canonical_vehicle) if e.canonical_vehicle else None,
        }
        for e in entries
    ]


@router.post("/vehicles/{vehicle_id}/draft-message")
def api_draft_message(
    vehicle_id: int, data: MessageDraftRequest, db: Session = Depends(get_db)
):
    vehicle = db.get(CanonicalVehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    message = draft_dealer_message(vehicle, data)
    if vehicle.shortlist_entry:
        vehicle.shortlist_entry.draft_message = message
        db.commit()
    return {"message": message, "auto_send": False}


@router.post("/dedup/merge")
def api_merge(data: MergeRequest, db: Session = Depends(get_db)):
    vehicle = IngestionService(db).manual_merge(data.listing_ids, data.notes)
    return {"canonical_vehicle_id": vehicle.id}


@router.post("/dedup/unmerge")
def api_unmerge(data: UnmergeRequest, db: Session = Depends(get_db)):
    vehicle = IngestionService(db).manual_unmerge(data.listing_id)
    return {"canonical_vehicle_id": vehicle.id}


@router.post("/collectors/run")
def api_run_collectors(source: str | None = None):
    if source:
        return run_collector(source)
    return run_all_collectors()


@router.post("/alerts/digest")
def api_trigger_digest():
    send_daily_digest_job()
    return {"ok": True}


@router.get("/collectors/runs")
def api_collector_runs(db: Session = Depends(get_db)):
    runs = (
        db.execute(select(CollectorRun).order_by(CollectorRun.started_at.desc()).limit(50))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "source": r.source,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "success": r.success,
            "listings_found": r.listings_found,
            "listings_new": r.listings_new,
            "error_message": r.error_message,
            "parser_broken": r.parser_broken,
        }
        for r in runs
    ]
