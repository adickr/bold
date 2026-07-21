"""Shortlist and dealer message drafting."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import AuditLog, CanonicalVehicle, ShortlistEntry, ShortlistStatus
from app.schemas.listings import MessageDraftRequest, ShortlistCreate, ShortlistUpdate


class ShortlistService:
    def __init__(self, db: Session):
        self.db = db

    def add(self, data: ShortlistCreate) -> ShortlistEntry:
        existing = self.db.execute(
            select(ShortlistEntry).where(
                ShortlistEntry.canonical_vehicle_id == data.canonical_vehicle_id
            )
        ).scalar_one_or_none()
        if existing:
            return existing
        entry = ShortlistEntry(
            canonical_vehicle_id=data.canonical_vehicle_id,
            status=data.status,
            interest_level=data.interest_level,
            notes=data.notes,
        )
        self.db.add(entry)
        self.db.add(
            AuditLog(
                action="shortlist_add",
                entity_type="canonical_vehicle",
                entity_id=data.canonical_vehicle_id,
                details={"status": data.status},
            )
        )
        vehicle = self.db.get(CanonicalVehicle, data.canonical_vehicle_id)
        if vehicle:
            entry.draft_message = draft_dealer_message(vehicle)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def update(self, entry_id: int, data: ShortlistUpdate) -> ShortlistEntry:
        entry = self.db.get(ShortlistEntry, entry_id)
        if not entry:
            raise ValueError("Shortlist entry not found")
        payload = data.model_dump(exclude_unset=True)
        for key, value in payload.items():
            setattr(entry, key, value)
        if data.status == ShortlistStatus.DEALER_CONTACTED.value:
            entry.dealer_contacted = True
        self.db.add(
            AuditLog(
                action="shortlist_update",
                entity_type="shortlist_entry",
                entity_id=entry_id,
                details=payload,
            )
        )
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def list_entries(self) -> list[ShortlistEntry]:
        return list(
            self.db.execute(
                select(ShortlistEntry).options(
                    selectinload(ShortlistEntry.canonical_vehicle)
                )
            )
            .scalars()
            .all()
        )

    def remove(self, entry_id: int) -> None:
        entry = self.db.get(ShortlistEntry, entry_id)
        if entry:
            self.db.delete(entry)
            self.db.add(
                AuditLog(
                    action="shortlist_remove",
                    entity_type="shortlist_entry",
                    entity_id=entry_id,
                )
            )
            self.db.commit()


def draft_dealer_message(
    vehicle: CanonicalVehicle, req: MessageDraftRequest | None = None
) -> str:
    req = req or MessageDraftRequest()
    year = vehicle.year or ""
    variant = vehicle.variant_normalised or "Fortuner 4x4"
    price = vehicle.current_lowest_price
    price_txt = f"R{price:,}" if price else "your listed price"
    days = vehicle.days_tracked or 0
    comps = vehicle.comparable_stats or {}
    median = comps.get("median_price")

    lines = [
        f"Hi, I’m interested in the {year} Fortuner {variant} you have listed at {price_txt}."
    ]
    if days:
        lines.append(
            f"I’ve been tracking similar vehicles and noticed this one has been on the market for about {days} days."
        )
    if median and price and price < median:
        lines.append(
            f"Based on current asking prices for comparable Fortuners (median around R{median:,}), I’m hoping we can find a mutually fair number."
        )
    if req.preferred_offer_zar:
        lines.append(
            f"Would you consider an offer of R{req.preferred_offer_zar:,} for a straightforward purchase"
            + (" with finance" if req.include_finance else " without finance complications")
            + (" including a trade-in" if req.include_trade_in else " without a trade-in")
            + "?"
        )
    else:
        lines.append(
            "I’m comparing a few similar vehicles and can move quickly if the vehicle checks out. "
            "Is there flexibility on the price for a straightforward purchase"
            + (" with a trade-in" if req.include_trade_in else " without a trade-in")
            + "?"
        )
    lines.append("")
    lines.append("Thanks for your time.")
    return "\n".join(lines)
