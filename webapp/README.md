# GTM Intelligence — Web App

A deterministic, server-rendered UI over the `skills/` analytics engine. It runs
skills as background jobs, stores runs/results in SQLite, and visualizes them —
no chat window, no LLM, no API keys.

## What's inside

| Surface | Route | What it does |
|---|---|---|
| Overview | `/` | System status, recent jobs & datasets, quick links |
| Marketing Mix | `/mmm` | Channel contribution (90% CI), response curves, baseline vs incremental, spend-vs-deals timeline |
| Budget What-if | `/mmm/whatif` | Project deals from a planned per-channel spend (reads fitted response curves; directional) |
| Data & Runs | `/data` | Trigger HubSpot pulls + the canonical MMM rebuild as background jobs; upload a budget workbook; run history with live status/logs |
| GTM Audit | `/audit` | Render the other analytical skills' result JSONs (trend, conversion, RF, clusters, cohorts, stage) |

Architecture: `main.py` (FastAPI) → routers → services → `jobs.py` (SQLite-backed
worker threads that run `python -m skills.…` subprocesses) → `db.py` (SQLite).
Skills are reused **unchanged**; the canonical MMM pipeline mirrors the
`marketing-mix-analyst` agent's command chain (`skills_registry.py`).

## Run locally

```bash
pip install -r requirements.txt -r requirements-web.txt
cp .env.example .env        # set HUBSPOT_TOKEN; leave APP_PASSWORD empty for no auth
uvicorn webapp.main:app --reload
# open http://localhost:8000
```

SQLite lives at `$PIPELINE_DATA_DIR/app.db` (default `./data/app.db`). The MMM
dashboard reads `data/results/mmm_marketing_mix_model.json` — if it's missing,
run **Rebuild Marketing Mix Model** from the Data & Runs page (set
`BUDGET_WORKBOOK_PATH` first, or upload a workbook).

## Deploy to Railway (later)

Build uses the repo `Dockerfile` (Python 3.11). See `railway.toml` for the full
checklist. The two things that matter:

1. **Attach a persistent Volume at `/data`** — SQLite + parquet need durable disk.
2. Set `PIPELINE_DATA_DIR=/data`, `HUBSPOT_TOKEN`, `APP_PASSWORD`, `SESSION_SECRET`.

## Auth

Single shared password via `APP_PASSWORD` (empty = disabled, local only).

**Deferred (later phase):** real multi-user accounts. All auth logic is isolated
in `webapp/auth.py`; adding a `user` table + per-user login is localized there.
