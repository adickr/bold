"""Modular alert providers and orchestration."""

from __future__ import annotations

import logging
import smtplib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.entities import AlertLog, CanonicalVehicle, SourceListing
from app.services.normalise import is_preferred_high_spec
from app.services.normalise import normalise_variant

logger = logging.getLogger(__name__)


class AlertProvider(ABC):
    name: str

    @abstractmethod
    def send(self, subject: str, body: str) -> bool:
        raise NotImplementedError


class LogAlertProvider(AlertProvider):
    name = "log"

    def send(self, subject: str, body: str) -> bool:
        logger.info("ALERT: %s\n%s", subject, body)
        return True


class EmailAlertProvider(AlertProvider):
    name = "email"

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, subject: str, body: str) -> bool:
        if not (
            self.settings.smtp_host
            and self.settings.smtp_from
            and self.settings.alert_email_to
        ):
            logger.warning("Email alert skipped: SMTP not configured")
            return False
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.smtp_from
        msg["To"] = self.settings.alert_email_to
        msg.set_content(body)
        password = (
            self.settings.smtp_password.get_secret_value()
            if self.settings.smtp_password
            else None
        )
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as smtp:
            smtp.starttls()
            if self.settings.smtp_user and password:
                smtp.login(self.settings.smtp_user, password)
            smtp.send_message(msg)
        return True


class TelegramAlertProvider(AlertProvider):
    name = "telegram"

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, subject: str, body: str) -> bool:
        token = (
            self.settings.telegram_bot_token.get_secret_value()
            if self.settings.telegram_bot_token
            else None
        )
        chat_id = self.settings.telegram_chat_id
        if not token or not chat_id:
            logger.warning("Telegram alert skipped: not configured")
            return False
        text = f"*{subject}*\n\n{body}"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=30,
        )
        response.raise_for_status()
        return True


def build_provider(settings: Settings) -> AlertProvider:
    if settings.alert_provider == "email":
        return EmailAlertProvider(settings)
    if settings.alert_provider == "telegram":
        return TelegramAlertProvider(settings)
    return LogAlertProvider()


class AlertService:
    def __init__(self, db: Session, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.provider = build_provider(self.settings)

    def _already_sent(self, dedupe_key: str, within_hours: int = 24) -> bool:
        since = datetime.now(timezone.utc) - timedelta(hours=within_hours)
        row = self.db.execute(
            select(AlertLog).where(
                AlertLog.dedupe_key == dedupe_key,
                AlertLog.sent_at >= since,
                AlertLog.success.is_(True),
            )
        ).scalar_one_or_none()
        return row is not None

    def _dispatch(
        self,
        alert_type: str,
        subject: str,
        body: str,
        *,
        canonical_vehicle_id: int | None = None,
        dedupe_key: str | None = None,
    ) -> bool:
        if dedupe_key and self._already_sent(dedupe_key):
            logger.info("Skipping duplicate alert %s", dedupe_key)
            return False
        try:
            ok = self.provider.send(subject, body)
        except Exception:
            logger.exception("Alert provider failed")
            ok = False
        self.db.add(
            AlertLog(
                alert_type=alert_type,
                channel=self.provider.name,
                subject=subject,
                body=body,
                canonical_vehicle_id=canonical_vehicle_id,
                dedupe_key=dedupe_key,
                success=ok,
            )
        )
        self.db.flush()
        return ok

    def maybe_alert_new_listing(
        self, vehicle: CanonicalVehicle, listing: SourceListing
    ) -> None:
        variant = normalise_variant(
            title=listing.title,
            variant_raw=listing.variant_raw,
            year=listing.year,
            colour=listing.colour,
            drivetrain_hint=listing.drivetrain,
        )
        high_spec = is_preferred_high_spec(variant)
        price = listing.price_zar
        interesting = high_spec or (
            price is not None and price <= self.settings.max_price_zar and variant.desirability_rank <= 3
        )
        stretch_compelling = (
            vehicle.is_stretch_candidate
            and high_spec
            and price is not None
            and price <= self.settings.stretch_price_zar
        )
        if not (interesting or stretch_compelling):
            return

        # Deduplicate alerts per physical vehicle
        dedupe = f"new:{vehicle.id}"
        label = variant.trim or variant.variant_normalised
        subject = f"New Fortuner: {listing.year} {label}"
        price_line = f"Price: R{price:,}" if price is not None else "Price: unknown"
        mileage_line = (
            f"Mileage: {listing.mileage_km:,} km"
            if listing.mileage_km is not None
            else "Mileage: unknown"
        )
        body = "\n".join(
            [
                "New listing detected",
                f"Year: {listing.year}",
                f"Variant: {listing.variant_normalised}",
                price_line,
                mileage_line,
                f"Dealer: {listing.dealer_name}",
                f"Source: {listing.source}",
                f"URL: {listing.url}",
                f"Stretch: {vehicle.is_stretch_candidate}",
                "Confirmed fields: source listing data. Scores are inferred.",
            ]
        )
        self._dispatch(
            "new_listing",
            subject,
            body,
            canonical_vehicle_id=vehicle.id,
            dedupe_key=dedupe,
        )

    def maybe_alert_price_reduction(
        self, vehicle: CanonicalVehicle, reduction_zar: int
    ) -> None:
        shortlisted = vehicle.shortlist_entry is not None
        multi = (vehicle.total_reduction_zar or 0) >= self.settings.price_reduction_alert_zar * 2
        if not (
            reduction_zar >= self.settings.price_reduction_alert_zar
            and (shortlisted or multi or reduction_zar >= 20_000)
        ):
            # Always alert shortlisted >= threshold; also large reductions
            if not (shortlisted and reduction_zar >= self.settings.price_reduction_alert_zar):
                if reduction_zar < 20_000 and not multi:
                    return

        dedupe = f"reduction:{vehicle.id}:{vehicle.current_lowest_price}"
        subject = f"Price drop: {vehicle.year} {vehicle.variant_normalised}"
        body = (
            f"Price reduced by R{reduction_zar:,}\n"
            f"Current: R{vehicle.current_lowest_price:,}\n"
            f"Original: R{vehicle.original_price:,}\n"
            f"Total reduction: R{vehicle.total_reduction_zar:,}\n"
            f"Days tracked: {vehicle.days_tracked}\n"
        )
        self._dispatch(
            "price_reduction",
            subject,
            body,
            canonical_vehicle_id=vehicle.id,
            dedupe_key=dedupe,
        )

    def maybe_alert_high_deal_score(self, vehicle: CanonicalVehicle) -> None:
        if vehicle.deal_score is None:
            return
        if vehicle.deal_score < self.settings.deal_score_alert_threshold:
            return
        dedupe = f"dealscore:{vehicle.id}:{int(vehicle.deal_score)}"
        subject = f"High deal score {vehicle.deal_score:.0f}: {vehicle.year} {vehicle.variant_normalised}"
        body = (
            f"Deal score: {vehicle.deal_score}/100 (inferred)\n"
            f"Price: R{vehicle.current_lowest_price:,}\n"
            f"Motivation: {vehicle.motivation_level} (estimate)\n"
            f"Breakdown: {vehicle.deal_score_breakdown}\n"
        )
        self._dispatch(
            "high_deal_score",
            subject,
            body,
            canonical_vehicle_id=vehicle.id,
            dedupe_key=dedupe,
        )

    def send_parser_failure(self, source: str, message: str) -> None:
        self._dispatch(
            "parser_failure",
            f"Collector parser issue: {source}",
            message,
            dedupe_key=f"parser:{source}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}",
        )

    def send_daily_digest(self, summary: dict[str, Any]) -> None:
        subject = f"Fortuner daily digest — {summary.get('date')}"
        lines = [
            "Daily Fortuner market digest",
            "",
            f"New listings: {summary.get('new_count', 0)}",
            f"Price reductions: {summary.get('reduction_count', 0)}",
            f"Removed: {summary.get('removed_count', 0)}",
            f"Relisted: {summary.get('relisted_count', 0)}",
            f"Duplicates detected: {summary.get('duplicate_count', 0)}",
            "",
            "Top deals:",
        ]
        for i, deal in enumerate(summary.get("top_deals") or [], 1):
            lines.append(
                f"  {i}. {deal.get('year')} {deal.get('variant')} — "
                f"R{deal.get('price'):,} — score {deal.get('deal_score')}"
            )
        lines.append("")
        lines.append("Asking prices only — not confirmed selling prices.")
        self._dispatch("daily_digest", subject, "\n".join(lines), dedupe_key=f"digest:{summary.get('date')}")

    def send_weekly_report(self, report: dict[str, Any]) -> None:
        subject = f"Fortuner weekly report — {report.get('week_ending')}"
        body = "\n".join(f"{k}: {v}" for k, v in report.items())
        self._dispatch(
            "weekly_report",
            subject,
            body,
            dedupe_key=f"weekly:{report.get('week_ending')}",
        )
