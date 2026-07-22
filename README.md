# Fortuner Buying Agent

Private vehicle-buying agent that monitors the South African used-car market for Toyota Fortuner 4x4 listings.

This is **not** a public classifieds site. The core value is cross-site history, price transparency, deduplication, deal scoring, and dealer-motivation signals.

## Features (MVP)

- Independent collectors for **AutoTrader**, **Cars.co.za**, and **WeBuyCars** (multi-page; WC-biased search URLs)
- Hard filters: Toyota Fortuner, 4x4, ≤100 000 km (stretch mileage ~110 000 km). **No hard price cap** — comfort budget ~R700 000 is for scoring/alerts only; browse sorts cheapest first
- Browse defaults: Western Cape · 4x4 · ≤100 000 km · sort lowest price first (dashboard spotlights can still surface exceptional finds elsewhere)
- Observation history (never overwrite prior prices)
- Variant normalisation (VX, GR-S, 2.8 GD-6, etc.)
- Weighted cross-site deduplication (VIN / stock / images / dealer+mileage …)
- Deal score + motivation estimate with transparent breakdowns
- Shortlist with notes, offers, and dealer message drafts (manual send only)
- Daily digest via modular alert provider (log / email / Telegram)
- Password-protected SSR dashboard + JSON API
- Docker Compose deployment with PostgreSQL

## Requirements

- **Python 3.10+** (3.12 recommended). macOS system Python 3.9 will fail.
- Or Docker (no local Python needed)

## Quick start (local)

```bash
cp .env.example .env
cd backend

# Use Python 3.12 if your default python3 is older (common on macOS):
#   brew install python@3.12
python3.12 -m venv .venv   # or: python3 -m venv .venv  (if already 3.10+)
source .venv/bin/activate
python -V                  # should show 3.10+ / 3.12.x

pip install -r requirements.txt
mkdir -p data/raw
export PYTHONPATH=.
export DATABASE_URL=sqlite:///./data/fortuner.db
export COLLECTOR_MODE=fixture
export ENABLE_SCHEDULER=false
python scripts/seed.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/login** — default login `buyer` / `fortuner`.

## Live data collection

Sites are JS-heavy / bot-protected, so live mode uses Playwright (real Chromium).

**Cars.co.za** sits behind Cloudflare Turnstile. Headless Chromium is often blocked.
On your Mac, use a **persistent headed Chrome profile** (without the yellow
“controlled by automated test software” bar). If Cloudflare still loops on
“Verifying you are human…”, use the CDP option below.

```bash
cd ~/bold
git pull origin cursor/fortuner-buying-agent-f5cc
cd backend
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# recommended for Cars.co.za:
#   brew install --cask google-chrome

export PYTHONPATH=.
export COLLECTOR_MODE=live
export USE_PLAYWRIGHT=true
export ENABLE_SCHEDULER=false
export PLAYWRIGHT_HEADED=true
export PLAYWRIGHT_USER_DATA_DIR=./data/chrome-profile-cars
# Fresh profile if the old one is poisoned:
#   rm -rf ./data/chrome-profile ./data/chrome-profile-cars

# Delete AutoTrader rows with invented/broken URLs or filter-chip noise, then start the app
python scripts/fix_bad_autotrader_urls.py
python scripts/purge_autotrader_noise.py
lsof -ti:8000 | xargs kill -9 2>/dev/null
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 (`buyer` / `fortuner`) → **Collect live listings now**.

Cars.co.za opens **one** search page (listing cards only). If you see a checkbox,
click it once. If it only spins on “Verifying…” and never finishes, stop the collect
and use a normal Chrome via CDP instead:

```bash
# Terminal A — real Chrome (no automation banner)
mkdir -p data/chrome-cdp
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/data/chrome-cdp"
# In that window: open cars.co.za, pass the check once, leave Chrome open.

# Terminal B — app
export PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Notes:
- Keep volume low; this is a private tool
- Listings outside 4x4 / ~110k km are filtered out (price is not a hard reject)
- Asking prices only — not sold prices
- Marketplace buttons only show for real scraped detail URLs (AutoTrader slugs are never invented — wrong slug → site error)
- Cars.co.za scrapes **search listing cards only** (never opens each car’s detail page)
- If Cars.co.za still fails, AutoTrader + WeBuyCars still feed the dashboard


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
