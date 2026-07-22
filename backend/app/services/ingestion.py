"""Ingest listings: persist observations, dedupe, score, alert."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.alerts.service import AlertService
from app.config import Settings, get_settings
from app.models.entities import (
    CanonicalVehicle,
    CollectorRun,
    DuplicateMatchEvidence,
    ListingObservation,
    ListingStatus,
    PriceEvent,
    SourceListing,
)
from app.schemas.listings import ListingPayload
from app.services.criteria import evaluate_listing
from app.services.dedup import score_pair, should_auto_merge, strong_identity_match
from app.services.market import compute_comparable_stats, refresh_market_fields
from app.services.media import absolute_url, normalise_image_urls, normalise_listing_url
from app.services.scoring import compute_deal_score, compute_motivation_score

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _max_dt(*values: datetime | None) -> datetime | None:
    aware_vals = [d for d in (_aware(v) for v in values) if d is not None]
    return max(aware_vals) if aware_vals else None


def _min_dt(*values: datetime | None) -> datetime | None:
    aware_vals = [d for d in (_aware(v) for v in values) if d is not None]
    return min(aware_vals) if aware_vals else None


class IngestionService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.alerts = AlertService(db, self.settings)

    def ingest_payloads(
        self, source: str, payloads: list[ListingPayload], *, full_scan: bool = True
    ) -> dict[str, Any]:
        run = CollectorRun(source=source, started_at=_utcnow())
        self.db.add(run)
        self.db.flush()

        seen_ids: set[str] = set()
        new_count = 0
        updated_count = 0
        accepted_listings: list[SourceListing] = []

        try:
            for payload in payloads:
                # Marketplace said sale-in-progress / reserved / sold — keep history but deactivate
                if (payload.availability or "available").lower() in {
                    "unavailable",
                    "reserved",
                    "sold",
                    "removed",
                }:
                    seen_ids.add(payload.source_listing_id)
                    self._mark_payload_unavailable(payload)
                    continue

                result = evaluate_listing(payload, self.settings)
                if not result.accepted or not result.variant:
                    # Drop already-stored noise immediately (don't wait for miss counters)
                    self._reject_existing_payload(payload, result.reasons)
                    continue
                # Repair / reject broken marketplace URLs before persist
                fixed_url = normalise_listing_url(
                    payload.source,
                    payload.url,
                    listing_id=payload.source_listing_id,
                    title=payload.title,
                    variant=payload.variant_raw,
                    year=payload.year,
                )
                if not fixed_url:
                    logger.warning(
                        "Skipping %s/%s — no valid marketplace URL (%s)",
                        payload.source,
                        payload.source_listing_id,
                        payload.url,
                    )
                    self._reject_existing_payload(payload, ["invalid_url"])
                    continue
                payload.url = fixed_url
                seen_ids.add(payload.source_listing_id)
                listing, created, changed = self._upsert_listing(payload, result)
                if created:
                    new_count += 1
                elif changed:
                    updated_count += 1
                accepted_listings.append(listing)

            if full_scan:
                self._mark_missing(source, seen_ids)
            for listing in accepted_listings:
                self._assign_canonical(listing)

            # Fix prior over-merges (same marketplace, different ads glued together)
            self._repair_same_source_merges()
            self._purge_listings_failing_criteria()
            self._scrub_bogus_price_events()
            self._scrub_all_price_aggregates()
            self._repair_listing_urls()

            self._rescore_active()
            run.success = True
            run.listings_found = len(accepted_listings)
            run.listings_new = new_count
            run.listings_updated = updated_count
            run.finished_at = _utcnow()
            self.db.commit()
            return {
                "source": source,
                "found": len(accepted_listings),
                "new": new_count,
                "updated": updated_count,
                "success": True,
            }
        except Exception as exc:
            logger.exception("Ingestion failed for %s", source)
            run.success = False
            run.error_message = str(exc)
            run.finished_at = _utcnow()
            self.db.commit()
            raise

    def record_parser_failure(self, source: str, message: str) -> None:
        run = CollectorRun(
            source=source,
            started_at=_utcnow(),
            finished_at=_utcnow(),
            success=False,
            error_message=message,
            parser_broken=True,
        )
        self.db.add(run)
        self.db.commit()
        self.alerts.send_parser_failure(source, message)

    def _upsert_listing(self, payload: ListingPayload, criteria) -> tuple[SourceListing, bool, bool]:
        variant = criteria.variant
        stmt = select(SourceListing).where(
            SourceListing.source == payload.source,
            SourceListing.source_listing_id == payload.source_listing_id,
        )
        listing = self.db.execute(stmt).scalar_one_or_none()
        now = _utcnow()
        created = listing is None
        changed_fields: list[str] = []

        if listing is None:
            listing = SourceListing(
                source=payload.source,
                source_listing_id=payload.source_listing_id,
                url=payload.url,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.db.add(listing)
            changed_fields = ["created"]
        else:
            # Detect changes
            for field, new_val in [
                ("price_zar", payload.price_zar),
                ("mileage_km", payload.mileage_km),
                ("title", payload.title),
                ("description", payload.description),
                ("dealer_name", payload.dealer_name),
            ]:
                old_val = getattr(listing, field)
                if new_val is not None and old_val != new_val:
                    changed_fields.append(field)
                    if field == "price_zar" and old_val is not None and new_val < old_val:
                        # price reduction handled after flush via canonical
                        pass

            was_removed = listing.listing_status in {
                ListingStatus.REMOVED.value,
                ListingStatus.POSSIBLY_REMOVED.value,
            }
            if was_removed:
                changed_fields.append("relisted")
                listing.listing_status = ListingStatus.RELISTED.value

        # Apply fields
        listing.url = (
            normalise_listing_url(
                payload.source,
                payload.url,
                listing_id=payload.source_listing_id,
                title=payload.title,
                variant=payload.variant_raw,
                year=payload.year,
            )
            or absolute_url(payload.url, source=payload.source)
            or payload.url
        )
        listing.title = payload.title
        listing.description = payload.description
        listing.dealer_name = payload.dealer_name
        listing.dealer_phone = payload.dealer_phone
        listing.dealer_location = payload.dealer_location
        listing.dealer_stock_number = payload.dealer_stock_number
        listing.year = payload.year
        listing.make = payload.make or "Toyota"
        listing.model = payload.model or "Fortuner"
        listing.variant_raw = payload.variant_raw
        listing.variant_normalised = variant.variant_normalised
        listing.engine = variant.engine
        listing.transmission = variant.transmission
        listing.drivetrain = variant.drivetrain
        listing.fuel_type = variant.fuel_type
        listing.trim = variant.trim
        listing.special_edition = variant.special_edition
        listing.generation = variant.generation
        old_price = listing.price_zar
        listing.price_zar = payload.price_zar
        listing.mileage_km = payload.mileage_km
        listing.colour = payload.colour
        listing.vin = payload.vin
        listing.registration = payload.registration
        listing.image_urls = normalise_image_urls(payload.image_urls, source=payload.source)
        listing.is_stretch_candidate = criteria.is_stretch
        listing.risk_flags = criteria.risk_flags
        listing.raw_payload = payload.raw_payload
        listing.last_seen_at = now
        listing.consecutive_misses = 0
        if listing.listing_status != ListingStatus.RELISTED.value:
            listing.listing_status = ListingStatus.ACTIVE.value

        self.db.flush()

        obs = ListingObservation(
            source_listing_id=listing.id,
            observed_at=now,
            price_zar=listing.price_zar,
            mileage_km=listing.mileage_km,
            title=listing.title,
            description=listing.description,
            dealer_name=listing.dealer_name,
            source=listing.source,
            availability_status=listing.listing_status,
            image_urls=listing.image_urls,
            changed_fields=changed_fields,
        )
        self.db.add(obs)

        if (
            not created
            and old_price is not None
            and listing.price_zar is not None
            and listing.price_zar != old_price
            and listing.canonical_vehicle_id
        ):
            self._record_price_change(listing, old_price, listing.price_zar)

        return listing, created, bool(changed_fields)

    def _reject_existing_payload(self, payload: ListingPayload, reasons: list[str]) -> None:
        """Immediately deactivate a stored listing that no longer meets buyer criteria."""
        listing = self.db.execute(
            select(SourceListing).where(
                SourceListing.source == payload.source,
                SourceListing.source_listing_id == payload.source_listing_id,
            )
        ).scalar_one_or_none()
        if listing is None:
            return
        if listing.listing_status != ListingStatus.REMOVED.value:
            logger.info(
                "Rejecting %s/%s (%s) — marking removed",
                payload.source,
                payload.source_listing_id,
                ",".join(reasons) or "criteria",
            )
            listing.listing_status = ListingStatus.REMOVED.value
            listing.last_seen_at = _utcnow()
            # Clear false 4x4 tags so browse filters don't keep surfacing them
            reason_blob = " ".join(reasons or [])
            if any(
                key in reason_blob
                for key in ("4x2", "drivetrain_unclear", "non_4x4", "not_fortuner")
            ):
                if listing.drivetrain == "4x4":
                    listing.drivetrain = None
        if listing.canonical_vehicle_id:
            vehicle = self.db.get(CanonicalVehicle, listing.canonical_vehicle_id)
            if vehicle:
                self._refresh_vehicle_active_flag(vehicle)
                # If every linked ad is non-4x4 / unclear, force inactive
                linked = list(
                    self.db.execute(
                        select(SourceListing).where(
                            SourceListing.canonical_vehicle_id == vehicle.id
                        )
                    )
                    .scalars()
                    .all()
                )
                if not any(
                    (x.listing_status or "")
                    in {ListingStatus.ACTIVE.value, ListingStatus.RELISTED.value}
                    and (x.drivetrain or "").lower() == "4x4"
                    for x in linked
                ):
                    vehicle.is_active = False
                    if (vehicle.drivetrain or "").lower() == "4x4":
                        vehicle.drivetrain = listing.drivetrain

    def _payload_from_listing(self, listing: SourceListing) -> ListingPayload:
        """Rebuild a criteria payload; re-detect axle from URL/title (ignore stale 4x4 tags)."""
        drivetrain = listing.drivetrain
        if listing.source == "cars_co_za":
            from app.collectors.cars_co_za import CarsCoZaCollector

            drivetrain = CarsCoZaCollector._drivetrain_from_text(
                listing.url, listing.title, listing.variant_raw
            )
        elif listing.source == "autotrader":
            from app.collectors.autotrader import AutoTraderCollector
            from app.services.normalise import detect_drivetrain

            url_dt = AutoTraderCollector.drivetrain_from_url(listing.url)
            text_dt = detect_drivetrain(
                " ".join(filter(None, [listing.title, listing.variant_raw]))
            )
            if url_dt == "4x2" or text_dt == "4x2":
                drivetrain = "4x2"
            elif url_dt == "4x4" or text_dt == "4x4":
                drivetrain = "4x4"
            else:
                drivetrain = None  # stale search-scope 4x4 tag — reject
        return ListingPayload(
            source=listing.source,
            source_listing_id=listing.source_listing_id,
            url=listing.url or "",
            title=listing.title,
            description=listing.description,
            variant_raw=listing.variant_raw,
            year=listing.year,
            price_zar=listing.price_zar,
            mileage_km=listing.mileage_km,
            colour=listing.colour,
            vin=listing.vin,
            dealer_name=listing.dealer_name,
            dealer_location=listing.dealer_location,
            dealer_phone=listing.dealer_phone,
            dealer_stock_number=listing.dealer_stock_number,
            drivetrain=drivetrain,
            transmission=listing.transmission,
            fuel_type=listing.fuel_type,
            make=listing.make or "Toyota",
            model=listing.model or "Fortuner",
            image_urls=listing.image_urls or [],
        )

    def _purge_listings_failing_criteria(self) -> int:
        """Re-check active ads (e.g. Cars.co.za 4x2 demos wrongly tagged 4x4)."""
        active = (
            self.db.execute(
                select(SourceListing).where(
                    SourceListing.listing_status.in_(
                        [
                            ListingStatus.ACTIVE.value,
                            ListingStatus.RELISTED.value,
                            ListingStatus.POSSIBLY_REMOVED.value,
                        ]
                    )
                )
            )
            .scalars()
            .all()
        )
        removed = 0
        for listing in active:
            payload = self._payload_from_listing(listing)
            result = evaluate_listing(payload, self.settings)
            if result.accepted:
                # Keep stored drivetrain honest for Cars.co.za
                if listing.source == "cars_co_za" and payload.drivetrain != listing.drivetrain:
                    listing.drivetrain = payload.drivetrain
                continue
            self._reject_existing_payload(payload, result.reasons)
            removed += 1
        if removed:
            logger.info("Purged %s listings that fail current buyer criteria", removed)
        return removed

    def _mark_payload_unavailable(self, payload: ListingPayload) -> None:
        """Deactivate a listing the marketplace flagged as reserved / sale-in-progress / sold."""
        listing = self.db.execute(
            select(SourceListing).where(
                SourceListing.source == payload.source,
                SourceListing.source_listing_id == payload.source_listing_id,
            )
        ).scalar_one_or_none()
        if listing is None:
            logger.info(
                "Unavailable %s/%s not in DB yet — skipping insert",
                payload.source,
                payload.source_listing_id,
            )
            return
        if listing.listing_status != ListingStatus.REMOVED.value:
            logger.info(
                "Marking %s/%s removed (marketplace availability=%s)",
                payload.source,
                payload.source_listing_id,
                payload.availability,
            )
            listing.listing_status = ListingStatus.REMOVED.value
            listing.last_seen_at = _utcnow()
        if listing.canonical_vehicle_id:
            vehicle = self.db.get(CanonicalVehicle, listing.canonical_vehicle_id)
            if vehicle:
                self._refresh_vehicle_active_flag(vehicle)

    def _refresh_vehicle_active_flag(self, vehicle: CanonicalVehicle) -> None:
        linked = list(vehicle.source_listings or [])
        vehicle.is_active = any(
            (x.listing_status or "")
            in {ListingStatus.ACTIVE.value, ListingStatus.RELISTED.value}
            for x in linked
        )

    def _mark_missing(self, source: str, seen_ids: set[str]) -> None:
        """Only mark removals after successful scan with findings (caller ensures non-empty)."""
        stmt = select(SourceListing).where(
            SourceListing.source == source,
            SourceListing.listing_status.in_(
                [
                    ListingStatus.ACTIVE.value,
                    ListingStatus.POSSIBLY_REMOVED.value,
                    ListingStatus.RELISTED.value,
                ]
            ),
        )
        active = self.db.execute(stmt).scalars().all()
        now = _utcnow()
        for listing in active:
            if listing.source_listing_id in seen_ids:
                continue
            listing.consecutive_misses += 1
            if listing.consecutive_misses >= self.settings.missed_scans_removed:
                listing.listing_status = ListingStatus.REMOVED.value
            elif listing.consecutive_misses >= self.settings.missed_scans_possibly_removed:
                listing.listing_status = ListingStatus.POSSIBLY_REMOVED.value
            # Also time-based
            last = _aware(listing.last_seen_at)
            if last and now - last >= timedelta(hours=self.settings.hours_until_removed):
                if listing.consecutive_misses >= self.settings.missed_scans_possibly_removed:
                    listing.listing_status = ListingStatus.REMOVED.value
            if listing.listing_status == ListingStatus.REMOVED.value and listing.canonical_vehicle_id:
                vehicle = self.db.get(CanonicalVehicle, listing.canonical_vehicle_id)
                if vehicle:
                    self._refresh_vehicle_active_flag(vehicle)

    def _assign_canonical(self, listing: SourceListing) -> CanonicalVehicle:
        if listing.canonical_vehicle_id:
            vehicle = self.db.get(CanonicalVehicle, listing.canonical_vehicle_id)
            if vehicle:
                self._sync_canonical_from_listing(vehicle, listing)
                return vehicle

        # Find merge candidates among recent active listings
        candidates = (
            self.db.execute(
                select(SourceListing)
                .where(
                    SourceListing.id != listing.id,
                    SourceListing.canonical_vehicle_id.is_not(None),
                    SourceListing.listing_status.in_(
                        [
                            ListingStatus.ACTIVE.value,
                            ListingStatus.RELISTED.value,
                            ListingStatus.POSSIBLY_REMOVED.value,
                        ]
                    ),
                )
                .limit(500)
            )
            .scalars()
            .all()
        )

        best: tuple[SourceListing, Any] | None = None
        for other in candidates:
            # Cheap prefilter
            if listing.year and other.year and abs(listing.year - other.year) > 1:
                continue
            # Never soft-merge distinct ads from the same marketplace
            if (
                listing.source == other.source
                and listing.source_listing_id != other.source_listing_id
                and not strong_identity_match(listing, other)
            ):
                continue
            result = score_pair(listing, other, self.settings)
            if best is None or result.score > best[1].score:
                best = (other, result)

        if best and should_auto_merge(best[1], self.settings):
            other, result = best
            vehicle = self.db.get(CanonicalVehicle, other.canonical_vehicle_id)
            assert vehicle is not None
            listing.canonical_vehicle_id = vehicle.id
            self.db.add(
                DuplicateMatchEvidence(
                    canonical_vehicle_id=vehicle.id,
                    listing_a_id=listing.id,
                    listing_b_id=other.id,
                    score=result.score,
                    confidence=result.confidence,
                    evidence=result.evidence,
                )
            )
            self._sync_canonical_from_listing(vehicle, listing)
            vehicle.duplicate_match_confidence = result.confidence
            return vehicle

        return self._create_canonical_vehicle(listing, alert_new=True)

    def _create_canonical_vehicle(
        self, listing: SourceListing, *, alert_new: bool = False
    ) -> CanonicalVehicle:
        vehicle = CanonicalVehicle(
            year=listing.year,
            make=listing.make,
            model=listing.model,
            variant_normalised=listing.variant_normalised,
            engine=listing.engine,
            transmission=listing.transmission,
            drivetrain=listing.drivetrain,
            fuel_type=listing.fuel_type,
            trim=listing.trim,
            special_edition=listing.special_edition,
            generation=listing.generation,
            colour=listing.colour,
            vin=listing.vin,
            registration=listing.registration,
            current_lowest_price=listing.price_zar,
            lowest_observed_price=listing.price_zar,
            original_price=listing.price_zar,
            current_mileage_km=listing.mileage_km,
            first_seen_at=listing.first_seen_at,
            last_seen_at=listing.last_seen_at,
            risk_flags=listing.risk_flags or [],
            is_stretch_candidate=listing.is_stretch_candidate,
            primary_image_url=(listing.image_urls or [None])[0],
            primary_location=listing.dealer_location,
            primary_dealer=listing.dealer_name,
            source_count=1,
            is_active=True,
        )
        self.db.add(vehicle)
        self.db.flush()
        listing.canonical_vehicle_id = vehicle.id

        if listing.price_zar is not None:
            self.db.add(
                PriceEvent(
                    canonical_vehicle_id=vehicle.id,
                    source_listing_id=listing.id,
                    observed_at=listing.first_seen_at,
                    old_price_zar=None,
                    new_price_zar=listing.price_zar,
                    change_zar=0,
                    source=listing.source,
                    note="initial_price",
                )
            )

        if alert_new:
            self.alerts.maybe_alert_new_listing(vehicle, listing)
        return vehicle

    def _repair_same_source_merges(self) -> int:
        """Split canonicals that glued distinct same-source ads without VIN/reg/stock."""
        vehicles = list(
            self.db.execute(
                select(CanonicalVehicle).options(selectinload(CanonicalVehicle.source_listings))
            )
            .scalars()
            .all()
        )
        repaired = 0
        for vehicle in vehicles:
            linked = list(vehicle.source_listings or [])
            if len(linked) < 2:
                continue
            by_source: dict[str, list[SourceListing]] = {}
            for listing in linked:
                by_source.setdefault(listing.source, []).append(listing)

            for _source, group in by_source.items():
                if len(group) < 2:
                    continue
                clusters: list[list[SourceListing]] = []
                for listing in sorted(group, key=lambda x: x.id or 0):
                    placed = False
                    for cluster in clusters:
                        if any(strong_identity_match(listing, member) for member in cluster):
                            cluster.append(listing)
                            placed = True
                            break
                    if not placed:
                        clusters.append([listing])

                if len(clusters) <= 1:
                    continue

                # First cluster keeps the existing canonical; others get new vehicles
                for cluster in clusters[1:]:
                    for listing in cluster:
                        listing.canonical_vehicle_id = None
                    self.db.flush()
                    anchor = cluster[0]
                    new_vehicle = self._create_canonical_vehicle(anchor, alert_new=False)
                    for listing in cluster[1:]:
                        listing.canonical_vehicle_id = new_vehicle.id
                    self._reattach_price_events(
                        [x.id for x in cluster if x.id is not None], new_vehicle.id
                    )
                    self._sync_canonical_from_listing(new_vehicle, anchor)
                    repaired += len(cluster)

                stay = clusters[0]
                self._sync_canonical_from_listing(vehicle, stay[0])
                logger.info(
                    "Repaired same-source over-merge on canonical %s (%s clusters)",
                    vehicle.id,
                    len(clusters),
                )

            # Deactivate empty shells
            remaining = [
                x for x in (vehicle.source_listings or []) if x.canonical_vehicle_id == vehicle.id
            ]
            if not remaining:
                vehicle.is_active = False
            else:
                # Clear fake reductions left by the over-merge
                self._recompute_price_aggregates(vehicle, remaining)

        if repaired:
            logger.info("Split %s falsely merged same-source listing(s)", repaired)
        return repaired

    def _scrub_all_price_aggregates(self) -> None:
        """Recompute original/reduction for every vehicle (clears merge pollution)."""
        vehicles = list(
            self.db.execute(
                select(CanonicalVehicle).options(selectinload(CanonicalVehicle.source_listings))
            )
            .scalars()
            .all()
        )
        for vehicle in vehicles:
            linked = list(vehicle.source_listings or [])
            self._recompute_price_aggregates(vehicle, linked)

    def _scrub_bogus_price_events(self) -> int:
        """Drop price-change events that are garbage or not backed by listing history."""
        events = list(self.db.execute(select(PriceEvent)).scalars().all())
        removed = 0
        for event in events:
            prices = [p for p in (event.old_price_zar, event.new_price_zar) if p is not None]
            if any(not (50_000 <= p <= 5_000_000) for p in prices):
                self.db.delete(event)
                removed += 1
                continue
            if (
                event.change_zar is not None
                and event.change_zar < 0
                and event.source_listing_id is not None
            ):
                listing = self.db.get(SourceListing, event.source_listing_id)
                if listing is None:
                    self.db.delete(event)
                    removed += 1
                    continue
                first_price, current_price, _ = self._listing_price_span(listing)
                genuine = 0
                if first_price is not None and current_price is not None:
                    genuine = max(0, first_price - current_price)
                claimed = abs(event.change_zar)
                # Stale merge / parse flap: event claims a cut the listing never lived
                if genuine == 0 or claimed > genuine + 5_000:
                    self.db.delete(event)
                    removed += 1
        if removed:
            logger.info("Removed %s bogus price events", removed)
            self.db.flush()
        return removed

    def _repair_listing_urls(self) -> int:
        """Rebuild short/invalid marketplace URLs already in the DB."""
        listings = list(self.db.execute(select(SourceListing)).scalars().all())
        fixed = 0
        for listing in listings:
            repaired = normalise_listing_url(
                listing.source,
                listing.url,
                listing_id=listing.source_listing_id,
                title=listing.title,
                variant=listing.variant_raw or listing.variant_normalised,
                year=listing.year,
            )
            if repaired and repaired != listing.url:
                listing.url = repaired
                fixed += 1
            elif not repaired:
                # Hide broken outbound links from the UI
                if listing.listing_status in {
                    ListingStatus.ACTIVE.value,
                    ListingStatus.RELISTED.value,
                }:
                    listing.listing_status = ListingStatus.POSSIBLY_REMOVED.value
                    fixed += 1
        if fixed:
            logger.info("Repaired/hid %s listing URL(s)", fixed)
            self.db.flush()
        return fixed

    def _reattach_price_events(self, listing_ids: list[int], canonical_id: int) -> None:
        if not listing_ids:
            return
        events = (
            self.db.execute(select(PriceEvent).where(PriceEvent.source_listing_id.in_(listing_ids)))
            .scalars()
            .all()
        )
        for event in events:
            event.canonical_vehicle_id = canonical_id

    def _recompute_price_aggregates(
        self, vehicle: CanonicalVehicle, linked: list[SourceListing]
    ) -> None:
        """Set current/original/reduction from same-listing observation history.

        Cross-listing ask spreads and stale merge PriceEvents must never look
        like a seller price cut. A reduction is only first ask → current ask
        on the *same* marketplace ad.
        """
        active_linked = [
            x
            for x in linked
            if x.listing_status
            in {
                ListingStatus.ACTIVE.value,
                ListingStatus.RELISTED.value,
                ListingStatus.POSSIBLY_REMOVED.value,
            }
        ]
        prices = [x.price_zar for x in active_linked if x.price_zar is not None]
        if prices:
            vehicle.current_lowest_price = min(prices)
            vehicle.lowest_observed_price = min(
                filter(None, [vehicle.lowest_observed_price, min(prices)])
            )

        pool = active_linked or linked
        best_reduction = 0
        original_for_display: int | None = None
        last_cut_at: datetime | None = None

        if pool:
            earliest = min(
                pool,
                key=lambda x: _aware(x.first_seen_at) or datetime.max.replace(tzinfo=timezone.utc),
            )
            first_p, _, _ = self._listing_price_span(earliest)
            original_for_display = first_p or earliest.price_zar

        for listing in pool:
            first_price, current_price, cut_at = self._listing_price_span(listing)
            if first_price is None or current_price is None:
                continue
            if first_price > current_price:
                reduction = first_price - current_price
                if reduction > best_reduction:
                    best_reduction = reduction
                    original_for_display = first_price
                    last_cut_at = cut_at

        vehicle.original_price = original_for_display
        vehicle.total_reduction_zar = best_reduction
        vehicle.last_reduction_at = last_cut_at if best_reduction > 0 else None

    def _listing_price_span(
        self, listing: SourceListing
    ) -> tuple[int | None, int | None, datetime | None]:
        """First and latest ask on this listing alone; cut timestamp if it fell."""
        obs: list[ListingObservation] = []
        if listing.id is not None:
            obs = list(
                self.db.execute(
                    select(ListingObservation)
                    .where(ListingObservation.source_listing_id == listing.id)
                    .order_by(ListingObservation.observed_at.asc())
                )
                .scalars()
                .all()
            )
        priced: list[tuple[int, datetime | None]] = [
            (o.price_zar, o.observed_at) for o in obs if o.price_zar is not None
        ]
        if listing.price_zar is not None:
            if not priced or priced[-1][0] != listing.price_zar:
                priced.append((listing.price_zar, listing.last_seen_at))
        if not priced:
            return None, None, None
        first_price = priced[0][0]
        current_price = priced[-1][0]
        cut_at = None
        if current_price < first_price:
            for price, at in priced:
                if price == current_price:
                    cut_at = _aware(at)
                    break
        return first_price, current_price, cut_at

    def _sync_canonical_from_listing(
        self, vehicle: CanonicalVehicle, listing: SourceListing
    ) -> None:
        now = _utcnow()
        vehicle.last_seen_at = _max_dt(vehicle.last_seen_at, listing.last_seen_at) or now
        first = _min_dt(vehicle.first_seen_at, listing.first_seen_at) or listing.first_seen_at
        vehicle.first_seen_at = first
        if first:
            f = _aware(first)
            assert f is not None
            vehicle.days_tracked = max(0, (now - f).days)

        # Aggregate from all linked listings
        self.db.flush()
        linked = (
            self.db.execute(
                select(SourceListing).where(SourceListing.canonical_vehicle_id == vehicle.id)
            )
            .scalars()
            .all()
        )
        active_linked = [
            x
            for x in linked
            if x.listing_status
            in {
                ListingStatus.ACTIVE.value,
                ListingStatus.RELISTED.value,
                ListingStatus.POSSIBLY_REMOVED.value,
            }
        ]
        self._recompute_price_aggregates(vehicle, linked)

        mileages = [x.mileage_km for x in active_linked if x.mileage_km is not None]
        if mileages:
            vehicle.current_mileage_km = min(mileages)

        vehicle.source_count = len({x.source for x in linked})
        vehicle.is_stretch_candidate = any(x.is_stretch_candidate for x in linked)
        vehicle.is_active = any(
            x.listing_status
            in {ListingStatus.ACTIVE.value, ListingStatus.RELISTED.value}
            for x in linked
        )
        # Prefer cheapest active listing for display (trim does not win)
        primary = sorted(
            active_linked or linked,
            key=lambda x: (x.price_zar or 10**9, x.id or 0),
        )[0]
        vehicle.year = vehicle.year or primary.year
        vehicle.variant_normalised = primary.variant_normalised or vehicle.variant_normalised
        vehicle.engine = primary.engine or vehicle.engine
        vehicle.trim = primary.trim or vehicle.trim
        vehicle.special_edition = primary.special_edition or vehicle.special_edition
        vehicle.drivetrain = primary.drivetrain or vehicle.drivetrain
        vehicle.colour = primary.colour or vehicle.colour
        vehicle.primary_dealer = primary.dealer_name or vehicle.primary_dealer
        vehicle.primary_location = primary.dealer_location or vehicle.primary_location
        if primary.image_urls:
            vehicle.primary_image_url = primary.image_urls[0]
        risks = set(vehicle.risk_flags or [])
        for x in linked:
            risks.update(x.risk_flags or [])
        vehicle.risk_flags = sorted(risks)

    def _record_price_change(
        self, listing: SourceListing, old_price: int, new_price: int
    ) -> None:
        vehicle = self.db.get(CanonicalVehicle, listing.canonical_vehicle_id)
        if not vehicle:
            return
        change = new_price - old_price
        event = PriceEvent(
            canonical_vehicle_id=vehicle.id,
            source_listing_id=listing.id,
            observed_at=_utcnow(),
            old_price_zar=old_price,
            new_price_zar=new_price,
            change_zar=change,
            source=listing.source,
            note="price_change",
        )
        self.db.add(event)
        self.db.flush()
        linked = (
            self.db.execute(
                select(SourceListing).where(SourceListing.canonical_vehicle_id == vehicle.id)
            )
            .scalars()
            .all()
        )
        self._recompute_price_aggregates(vehicle, linked)
        if change < 0:
            self.alerts.maybe_alert_price_reduction(vehicle, abs(change))

    def _rescore_active(self) -> None:
        vehicles = (
            self.db.execute(
                select(CanonicalVehicle)
                .where(CanonicalVehicle.is_active.is_(True))
                .options(selectinload(CanonicalVehicle.source_listings))
            )
            .scalars()
            .all()
        )
        for vehicle in vehicles:
            stats = compute_comparable_stats(self.db, vehicle)
            vehicle.comparable_stats = stats
            deal, breakdown = compute_deal_score(
                vehicle, comparable_median=stats.get("median_price"), settings=self.settings
            )
            vehicle.deal_score = deal
            vehicle.deal_score_breakdown = breakdown
            dealer_count = 0
            if vehicle.primary_dealer:
                dealer_count = sum(
                    1
                    for v in vehicles
                    if v.id != vehicle.id
                    and v.primary_dealer == vehicle.primary_dealer
                    and v.trim == vehicle.trim
                )
            mot, level, mot_break = compute_motivation_score(
                vehicle, dealer_similar_count=dealer_count, settings=self.settings
            )
            vehicle.motivation_score = mot
            vehicle.motivation_level = level
            vehicle.motivation_breakdown = mot_break
            refresh_market_fields(vehicle)
            self.alerts.maybe_alert_high_deal_score(vehicle)

    def manual_merge(self, listing_ids: list[int], notes: str | None = None) -> CanonicalVehicle:
        listings = (
            self.db.execute(select(SourceListing).where(SourceListing.id.in_(listing_ids)))
            .scalars()
            .all()
        )
        if len(listings) < 2:
            raise ValueError("Need at least two listings to merge")

        # Prefer existing canonical with most listings
        vehicles: dict[int, CanonicalVehicle] = {}
        for listing in listings:
            if listing.canonical_vehicle_id:
                v = self.db.get(CanonicalVehicle, listing.canonical_vehicle_id)
                if v:
                    vehicles[v.id] = v
        if vehicles:
            target = max(vehicles.values(), key=lambda v: v.source_count or 0)
        else:
            target = CanonicalVehicle(make="Toyota", model="Fortuner", is_active=True)
            self.db.add(target)
            self.db.flush()

        for listing in listings:
            old_id = listing.canonical_vehicle_id
            listing.canonical_vehicle_id = target.id
            if old_id and old_id != target.id:
                orphan = self.db.get(CanonicalVehicle, old_id)
                if orphan and not orphan.source_listings:
                    orphan.is_active = False
            self.db.add(
                DuplicateMatchEvidence(
                    canonical_vehicle_id=target.id,
                    listing_a_id=listing.id,
                    listing_b_id=listings[0].id,
                    score=100,
                    confidence="certain",
                    evidence={"manual": True, "notes": notes},
                    is_manual=True,
                )
            )
            self._sync_canonical_from_listing(target, listing)

        target.duplicate_match_confidence = "certain"
        self.db.commit()
        self.db.refresh(target)
        return target

    def manual_unmerge(self, listing_id: int) -> CanonicalVehicle:
        listing = self.db.get(SourceListing, listing_id)
        if not listing:
            raise ValueError("Listing not found")
        old_vehicle_id = listing.canonical_vehicle_id
        listing.canonical_vehicle_id = None
        self.db.flush()
        vehicle = self._assign_canonical(listing)
        if old_vehicle_id:
            old = self.db.get(CanonicalVehicle, old_vehicle_id)
            if old:
                remaining = [x for x in old.source_listings if x.id != listing_id]
                if not remaining:
                    old.is_active = False
                else:
                    self._sync_canonical_from_listing(old, remaining[0])
        self.db.commit()
        return vehicle
