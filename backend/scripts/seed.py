#!/usr/bin/env python3
"""Seed the database from fixture collectors (demo / first boot)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.session import SessionLocal, init_db
from app.workers.scheduler import run_all_collectors


def main() -> None:
    settings = get_settings()
    Path(settings.raw_snapshot_dir).mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(parents=True, exist_ok=True)
    init_db()
    results = run_all_collectors()
    for row in results:
        print(row)
    db = SessionLocal()
    try:
        from sqlalchemy import func, select
        from app.models.entities import CanonicalVehicle, SourceListing

        vehicles = db.execute(select(func.count(CanonicalVehicle.id))).scalar()
        listings = db.execute(select(func.count(SourceListing.id))).scalar()
        print(f"Seed complete: {listings} source listings → {vehicles} canonical vehicles")
    finally:
        db.close()


if __name__ == "__main__":
    main()
