#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
export COLLECTOR_MODE="${COLLECTOR_MODE:-fixture}"
export ENABLE_SCHEDULER="${ENABLE_SCHEDULER:-false}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///./data/fortuner.db}"
mkdir -p data/raw
echo "Starting on http://127.0.0.1:8000  (login page at /login)"
exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
