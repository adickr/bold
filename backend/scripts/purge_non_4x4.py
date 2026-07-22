#!/usr/bin/env python3
"""Deactivate listings that fail current buyer criteria (e.g. Cars.co.za 4x2 demos)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal, init_db
from app.services.ingestion import IngestionService


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        service = IngestionService(db)
        removed = service._purge_listings_failing_criteria()
        service._scrub_all_price_aggregates()
        db.commit()
        print(f"Deactivated {removed} listing(s) that fail buyer criteria (need explicit 4x4).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
