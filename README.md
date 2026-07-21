# Fortuner Buying Agent

Private vehicle-buying agent that monitors the South African used-car market for Toyota Fortuner 4x4 listings.

This is **not** a public classifieds site. The core value is cross-site history, price transparency, deduplication, deal scoring, and dealer-motivation signals.

## Features (MVP)

- Independent collectors for **AutoTrader**, **Cars.co.za**, and **WeBuyCars**
- Hard filters: Toyota Fortuner, 4x4, ≤100 000 km, ≤R700 000 (stretch up to R725 000 / ~110 000 km)
- Observation history (never overwrite prior prices)
- Variant normalisation (VX, GR-S, 2.8 GD-6, etc.)
- Weighted cross-site deduplication (VIN / stock / images / dealer+mileage …)
- Deal score + motivation estimate with transparent breakdowns
- Shortlist with notes, offers, and dealer message drafts (manual send only)
- Daily digest via modular alert provider (log / email / Telegram)
- Password-protected SSR dashboard + JSON API
- Docker Compose deployment with PostgreSQL

## Quick start (local)

```bash
cp .env.example .env
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p ../data/raw
export PYTHONPATH=.
export DATABASE_URL=sqlite:///./data/fortuner.db
export COLLECTOR_MODE=fixture
export ENABLE_SCHEDULER=false
python scripts/seed.py
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — default login `buyer` / `fortuner`.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

API/UI: http://localhost:8000

## Collector modes

| Mode | Behaviour |
|------|-----------|
| `fixture` | Load sample listings (default for demo/CI) |
| `live` | Fetch public search pages with conservative delays |
| `hybrid` | Try live, fall back to fixtures on failure/empty |

Live collection is low-volume, respects failures independently, and does **not** bypass CAPTCHAs, auth, or access controls. Prefer official feeds when available.

## Scheduling (configurable)

- Marketplace collectors: every 3 hours
- Market summary: daily
- Daily digest: 06:00 UTC
- Weekly report: weekly

Disable with `ENABLE_SCHEDULER=false`.

## API highlights

All `/api/*` routes (except `/api/public/health`) require HTTP Basic auth.

- `GET /api/dashboard`
- `GET /api/vehicles`
- `GET /api/vehicles/{id}`
- `POST /api/shortlist`
- `POST /api/dedup/merge` / `POST /api/dedup/unmerge`
- `POST /api/collectors/run`
- `POST /api/alerts/digest`

Interactive docs: `/api/docs`

## Confirmed vs inferred

The UI labels:

- **Confirmed**: listing fields from sources (price, mileage, URLs)
- **Inferred**: deal score, motivation estimate, comparable medians (asking prices only)

## Tests

```bash
cd backend
PYTHONPATH=. pytest -q
```

## Legal / ops notes

- Private single-buyer tool; keep password-protected
- Link back to original listings; do not republish full advert copy publicly
- Review marketplace terms before scaling or commercialising
- Secrets via environment variables; scraper admin endpoints are auth-gated

## Project layout

```
backend/
  app/
    collectors/     # source adapters
    services/       # criteria, dedup, scoring, ingestion
    alerts/         # modular providers
    api/            # JSON + SSR routes
    models/         # SQLAlchemy entities
    workers/        # APScheduler jobs
  tests/
  scripts/seed.py
docker-compose.yml
```
