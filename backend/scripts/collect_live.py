#!/usr/bin/env python3
"""Run live collectors and ingest into the local database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Prefer live collection for this script unless explicitly overridden.
os.environ.setdefault("COLLECTOR_MODE", "live")
os.environ.setdefault("USE_PLAYWRIGHT", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")

from app.config import get_settings
from app.db.session import init_db
from app.workers.scheduler import run_all_collectors, run_collector


def main() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    Path(settings.raw_snapshot_dir).mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(parents=True, exist_ok=True)
    init_db()
    print(f"Collector mode: {settings.collector_mode}")
    print(f"Playwright: {settings.use_playwright}")
    source = sys.argv[1] if len(sys.argv) > 1 else None
    if source:
        results = [run_collector(source)]
    else:
        results = run_all_collectors()
    for row in results:
        print(row)
    failed = [r for r in results if not r.get("success")]
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
