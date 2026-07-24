"""Collection + digest scheduling."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from app.alerts.service import AlertService
from app.collectors import CollectorError, all_collectors, get_collector
from app.config import get_settings
from app.db.session import SessionLocal
from app.models.entities import (
    CanonicalVehicle,
    DuplicateMatchEvidence,
    ListingStatus,
    PriceEvent,
    SourceListing,
)
from app.services.ingestion import IngestionService

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def run_collector(source: str) -> dict[str, Any]:
    from app.services.search_profile import settings_for_active_search

    db = SessionLocal()
    try:
        settings = settings_for_active_search(db)
        ingestion = IngestionService(db, settings)
        collector = get_collector(source, settings=settings)
        with collector:
            try:
                payloads = collector.collect()
            except CollectorError as exc:
                ingestion.record_parser_failure(source, str(exc))
                return {"source": source, "success": False, "error": str(exc)}
            return ingestion.ingest_payloads(source, payloads)
    finally:
        db.close()


def run_all_collectors() -> list[dict[str, Any]]:
    results = []
    for collector in all_collectors():
        try:
            results.append(run_collector(collector.source))
        except Exception as exc:
            logger.exception("Collector %s failed independently", collector.source)
            results.append(
                {"source": collector.source, "success": False, "error": str(exc)}
            )
    return results


def build_daily_summary(db) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    new_count = db.execute(
        select(func.count(CanonicalVehicle.id)).where(CanonicalVehicle.first_seen_at >= since)
    ).scalar() or 0
    reduction_count = db.execute(
        select(func.count(PriceEvent.id)).where(
            PriceEvent.observed_at >= since, PriceEvent.change_zar < 0
        )
    ).scalar() or 0
    removed_count = db.execute(
        select(func.count(SourceListing.id)).where(
            SourceListing.listing_status == ListingStatus.REMOVED.value,
            SourceListing.updated_at >= since,
        )
    ).scalar() or 0
    relisted_count = db.execute(
        select(func.count(SourceListing.id)).where(
            SourceListing.listing_status == ListingStatus.RELISTED.value,
            SourceListing.updated_at >= since,
        )
    ).scalar() or 0
    duplicate_count = db.execute(
        select(func.count(DuplicateMatchEvidence.id)).where(
            DuplicateMatchEvidence.created_at >= since
        )
    ).scalar() or 0

    top = (
        db.execute(
            select(CanonicalVehicle)
            .where(CanonicalVehicle.is_active.is_(True), CanonicalVehicle.deal_score.is_not(None))
            .order_by(CanonicalVehicle.deal_score.desc())
            .limit(3)
        )
        .scalars()
        .all()
    )
    return {
        "date": now.strftime("%Y-%m-%d"),
        "new_count": new_count,
        "reduction_count": reduction_count,
        "removed_count": removed_count,
        "relisted_count": relisted_count,
        "duplicate_count": duplicate_count,
        "reductions_this_week": db.execute(
            select(func.count(PriceEvent.id)).where(
                PriceEvent.observed_at >= week_ago, PriceEvent.change_zar < 0
            )
        ).scalar()
        or 0,
        "top_deals": [
            {
                "id": v.id,
                "year": v.year,
                "variant": v.variant_normalised,
                "price": v.current_lowest_price,
                "deal_score": v.deal_score,
            }
            for v in top
        ],
    }


def send_daily_digest_job() -> None:
    db = SessionLocal()
    try:
        summary = build_daily_summary(db)
        AlertService(db).send_daily_digest(summary)
        db.commit()
    finally:
        db.close()


def market_summary_job() -> None:
    db = SessionLocal()
    try:
        from app.services.market_snapshot import record_market_snapshot

        record_market_snapshot(db)
    finally:
        db.close()


def weekly_report_job() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        actives = (
            db.execute(select(CanonicalVehicle).where(CanonicalVehicle.is_active.is_(True)))
            .scalars()
            .all()
        )
        prices = [v.current_lowest_price for v in actives if v.current_lowest_price]
        mileages = [v.current_mileage_km for v in actives if v.current_mileage_km]
        report = {
            "week_ending": now.strftime("%Y-%m-%d"),
            "active_matching": len(actives),
            "new_this_week": db.execute(
                select(func.count(CanonicalVehicle.id)).where(
                    CanonicalVehicle.first_seen_at >= week_ago
                )
            ).scalar()
            or 0,
            "removed_this_week": db.execute(
                select(func.count(SourceListing.id)).where(
                    SourceListing.listing_status == ListingStatus.REMOVED.value,
                    SourceListing.updated_at >= week_ago,
                )
            ).scalar()
            or 0,
            "price_reductions": db.execute(
                select(func.count(PriceEvent.id)).where(
                    PriceEvent.observed_at >= week_ago, PriceEvent.change_zar < 0
                )
            ).scalar()
            or 0,
            "median_price": int(median(prices)) if prices else None,
            "median_mileage": int(median(mileages)) if mileages else None,
            "median_days_on_market": float(median([v.days_tracked for v in actives]))
            if actives
            else None,
            "disclaimer": "Asking prices only — not confirmed selling prices.",
        }
        AlertService(db).send_weekly_report(report)
        db.commit()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        logger.info("Scheduler disabled")
        return None
    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_all_collectors,
        "interval",
        seconds=settings.marketplace_interval_seconds,
        id="collect_all",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=5),
    )
    scheduler.add_job(
        market_summary_job,
        "interval",
        seconds=settings.market_summary_interval_seconds,
        id="market_summary",
        replace_existing=True,
    )
    scheduler.add_job(
        send_daily_digest_job,
        "cron",
        hour=settings.daily_digest_hour_utc,
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        weekly_report_job,
        "interval",
        seconds=settings.weekly_report_interval_seconds,
        id="weekly_report",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
