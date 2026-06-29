"""SQLite storage for the web app (stdlib sqlite3 — no ORM dependency).

Connection-per-call with WAL mode keeps it safe across the FastAPI request
threads and the background job workers. Small result JSON is stored inline as
TEXT blobs; large parquet artifacts are kept on disk and referenced by path.

Schema ports cleanly to Postgres later (TEXT JSON blobs -> JSONB) if the app
ever goes multi-user / multi-instance.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,                 -- 'skill' | 'pipeline'
    name          TEXT NOT NULL,                 -- module path or pipeline key
    label         TEXT,
    params_json   TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued',-- queued|running|succeeded|failed
    envelope_json TEXT,                          -- parsed skill stdout envelope
    artifacts_json TEXT,                         -- {kind: path}
    logs_text     TEXT NOT NULL DEFAULT '',
    error         TEXT,
    run_id        TEXT,                          -- skills' run_id, if any
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    started_at    TEXT,
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS dataset (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity      TEXT NOT NULL,                   -- contacts|deals|spend|...
    source      TEXT NOT NULL,                   -- hubspot|workbook|upload
    parquet_path TEXT,
    n_records   INTEGER,
    params_json TEXT NOT NULL DEFAULT '{}',
    job_id      INTEGER,
    pulled_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS result_artifact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    skill_name  TEXT NOT NULL,
    result_json TEXT,                            -- full envelope results (inline)
    summary     TEXT,
    parquet_path TEXT,
    result_path TEXT,                            -- on-disk json path if present
    job_id      INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS spend_upload (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT,
    parsed_csv_path TEXT,
    channel_totals_json TEXT,
    period_range  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mmm_model (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    granularity     TEXT,
    n_periods       INTEGER,
    channel_groups_json TEXT,
    baseline_json   TEXT,
    incremental_json TEXT,
    channels_json   TEXT,
    diagnostics_json TEXT,
    result_path     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scenario (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mmm_run_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    spend_plan_json TEXT NOT NULL,
    projected_json TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        # On boot, any job left 'running' is orphaned (process died) -> fail it.
        # 'queued' jobs are re-enqueued by jobs.start_workers().
        conn.execute(
            "UPDATE job SET status='failed', error='orphaned (server restart)', "
            "finished_at=datetime('now') WHERE status='running'")
        conn.commit()
    finally:
        conn.close()


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run a write; return lastrowid."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def loads(val: str | None, default: Any = None) -> Any:
    if not val:
        return default
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default
