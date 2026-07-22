"""Background live-collection job with progress for the dashboard UI."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.collectors import all_collectors
from app.config import get_settings
from app.workers.scheduler import run_collector

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "current_source": None,
    "message": "Idle",
    "percent": 0,
    "sources": [],
    "ok": 0,
    "total": 0,
    "error": None,
}


def get_collect_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _set(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def _label(source: str) -> str:
    return {
        "autotrader": "AutoTrader",
        "cars_co_za": "Cars.co.za",
        "webuycars": "WeBuyCars",
    }.get(source, source)


def start_collect_job() -> dict[str, Any]:
    """Start a background collect if one is not already running."""
    with _lock:
        if _state["running"]:
            return dict(_state)

    # Help Cars.co.za clear Cloudflare on a local Mac (persistent Chrome cookies)
    settings = get_settings()
    if settings.playwright_headed or os.environ.get("PLAYWRIGHT_HEADED"):
        os.environ["PLAYWRIGHT_HEADED"] = "true"
    # Dedicated Cars profile — AutTrader/WeBuyCars must not thrash the same cookie jar
    profile = (
        os.environ.get("PLAYWRIGHT_USER_DATA_DIR")
        or settings.playwright_user_data_dir
        or "./data/chrome-profile-cars"
    )
    # Absolute path so relative ./data/... is stable regardless of cwd
    from pathlib import Path

    abs_profile = str(Path(profile).expanduser().resolve())
    Path(abs_profile).mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_USER_DATA_DIR"] = abs_profile
    if settings.playwright_cdp_url and not os.environ.get("PLAYWRIGHT_CDP_URL"):
        os.environ["PLAYWRIGHT_CDP_URL"] = settings.playwright_cdp_url
    logger.info(
        "Playwright profile: %s (headed=%s cdp=%s)",
        abs_profile,
        os.environ.get("PLAYWRIGHT_HEADED"),
        os.environ.get("PLAYWRIGHT_CDP_URL") or "-",
    )

    sources = [c.source for c in all_collectors()]
    source_rows = [
        {"source": s, "label": _label(s), "status": "pending", "found": None, "error": None}
        for s in sources
    ]
    _set(
        running=True,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        current_source=None,
        message="Starting live scan…",
        percent=2,
        sources=source_rows,
        ok=0,
        total=len(sources),
        error=None,
    )
    thread = threading.Thread(target=_run_job, name="collect-job", daemon=True)
    thread.start()
    return get_collect_status()


def _update_source(source: str, **kwargs: Any) -> None:
    with _lock:
        rows = list(_state["sources"])
        for row in rows:
            if row["source"] == source:
                row.update(kwargs)
                break
        _state["sources"] = rows


def _run_job() -> None:
    try:
        os.environ["COLLECTOR_MODE"] = "live"
        os.environ["USE_PLAYWRIGHT"] = "true"
        get_settings.cache_clear()

        sources = [c.source for c in all_collectors()]
        ok = 0
        for idx, source in enumerate(sources):
            _set(
                current_source=source,
                message=f"Scanning {_label(source)}…",
                percent=int(((idx) / max(len(sources), 1)) * 90) + 5,
            )
            _update_source(source, status="running")
            t0 = time.time()
            try:
                result = run_collector(source)
                success = bool(result.get("success"))
                found = result.get("found")
                if found is None:
                    found = result.get("listings_found")
                if success:
                    ok += 1
                    _update_source(
                        source,
                        status="done",
                        found=found,
                        new=result.get("new"),
                        updated=result.get("updated"),
                        error=None,
                        seconds=round(time.time() - t0, 1),
                    )
                else:
                    _update_source(
                        source,
                        status="failed",
                        found=found,
                        new=result.get("new"),
                        updated=result.get("updated"),
                        error=result.get("error") or "failed",
                        seconds=round(time.time() - t0, 1),
                    )
            except Exception as exc:
                logger.exception("Collect job source %s failed", source)
                _update_source(
                    source,
                    status="failed",
                    error=str(exc),
                    seconds=round(time.time() - t0, 1),
                )
            _set(
                ok=ok,
                percent=int(((idx + 1) / max(len(sources), 1)) * 95),
            )

        try:
            from app.db.session import SessionLocal
            from app.services.market_snapshot import record_market_snapshot

            db = SessionLocal()
            try:
                record_market_snapshot(db)
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to record market snapshot after collect")

        _set(
            running=False,
            finished_at=datetime.now(timezone.utc).isoformat(),
            current_source=None,
            message=f"Scan finished · {ok}/{len(sources)} sources succeeded",
            percent=100,
            ok=ok,
            total=len(sources),
        )
    except Exception as exc:
        logger.exception("Collect job crashed")
        _set(
            running=False,
            finished_at=datetime.now(timezone.utc).isoformat(),
            current_source=None,
            message="Scan failed",
            percent=100,
            error=str(exc),
        )
