"""Runtime configuration, sourced from environment (.env honored via dotenv).

All paths derive from PIPELINE_DATA_DIR so the web app shares the exact data
contract the skills use (data/raw, data/features, data/results). The SQLite DB
lives alongside that data so a single Railway volume persists everything.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Repo root = parent of the webapp/ package. Skills are invoked with this as cwd
# so `python -m skills.…` resolves.
REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("PIPELINE_DATA_DIR", str(REPO_ROOT / "data"))).resolve()
RESULTS_DIR = DATA_DIR / "results"
FEATURES_DIR = DATA_DIR / "features"
RAW_DIR = DATA_DIR / "raw"
for _d in (DATA_DIR, RESULTS_DIR, FEATURES_DIR, RAW_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("APP_DB_PATH", str(DATA_DIR / "app.db"))).resolve()

# Single shared password gate. If unset, auth is DISABLED (intended for local dev).
# NOTE (deferred): real multi-user accounts — a `user` table + per-user login —
# are a later phase. Keep all auth logic in webapp/auth.py so it slots in cleanly.
APP_PASSWORD = os.environ.get("APP_PASSWORD") or ""
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-insecure-change-me")

# HubSpot token is passed through to skill subprocesses (skills read it themselves).
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")

# Background worker pool. Small by default to respect HubSpot rate limits.
N_WORKERS = int(os.environ.get("N_WORKERS", "2"))

# Canonical MMM run id produced by the marketing-mix-analyst pipeline.
CANONICAL_MMM_RUN_ID = os.environ.get("CANONICAL_MMM_RUN_ID", "mmm")

# Path to the Apple Numbers budget workbook (for one-click re-ingest). Optional.
BUDGET_WORKBOOK_PATH = os.environ.get("BUDGET_WORKBOOK_PATH", "")
