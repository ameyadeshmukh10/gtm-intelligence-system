"""Background job runner — SQLite-backed, in-process worker threads (no Redis).

Each job runs one or more skills as subprocesses (`python -m <module> <json>`),
exactly how the agents invoke them, preserving the stdout-envelope contract.
Jobs persist in SQLite so the UI can poll status/logs and history survives.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import traceback
from typing import Any

from . import db
from .config import REPO_ROOT, HUBSPOT_TOKEN, N_WORKERS, DATA_DIR
from .skills_registry import PIPELINES

_q: "queue.Queue[int]" = queue.Queue()
_started = False
_SKILL_TIMEOUT = 60 * 30  # 30 min ceiling per skill


# ---------------------------------------------------------------- enqueue / boot
def enqueue(kind: str, name: str, params: dict | None = None,
            label: str | None = None) -> int:
    job_id = db.execute(
        "INSERT INTO job (kind, name, label, params_json, status) "
        "VALUES (?,?,?,?, 'queued')",
        (kind, name, label or name, json.dumps(params or {})))
    _q.put(job_id)
    return job_id


def start_workers() -> None:
    global _started
    if _started:
        return
    _started = True
    # Re-enqueue jobs that were queued when the server stopped.
    for row in db.query("SELECT id FROM job WHERE status='queued' ORDER BY id"):
        _q.put(row["id"])
    for i in range(max(1, N_WORKERS)):
        t = threading.Thread(target=_worker, name=f"job-worker-{i}", daemon=True)
        t.start()


def _worker() -> None:
    while True:
        job_id = _q.get()
        try:
            _run_job(job_id)
        except Exception:
            db.execute("UPDATE job SET status='failed', error=?, "
                       "finished_at=datetime('now') WHERE id=?",
                       (traceback.format_exc()[-2000:], job_id))
        finally:
            _q.task_done()


# ---------------------------------------------------------------- skill exec
def run_skill(module: str, params: dict) -> tuple[dict | None, str, int]:
    """Run a skill subprocess; return (parsed_envelope, combined_logs, returncode)."""
    env = {"PYTHONUNBUFFERED": "1"}
    import os
    env = {**os.environ, **env}
    env["PIPELINE_DATA_DIR"] = str(DATA_DIR)
    if HUBSPOT_TOKEN:
        env["HUBSPOT_TOKEN"] = HUBSPOT_TOKEN
    proc = subprocess.run(
        [sys.executable, "-m", module, json.dumps(params)],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=_SKILL_TIMEOUT)
    envelope = None
    out = (proc.stdout or "").strip()
    if out:
        # Skills print a single-line JSON envelope last; be tolerant of leading logs.
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    envelope = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
    logs = (f"$ python -m {module}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    return envelope, logs, proc.returncode


# ---------------------------------------------------------------- run a job
def _run_job(job_id: int) -> None:
    job = db.query_one("SELECT * FROM job WHERE id=?", (job_id,))
    if not job:
        return
    db.execute("UPDATE job SET status='running', started_at=datetime('now') WHERE id=?",
               (job_id,))
    params = db.loads(job["params_json"], {})
    try:
        if job["kind"] == "skill":
            envelope, logs, rc = run_skill(job["name"], params)
            _finish_skill(job_id, job["name"], params, envelope, logs, rc)
        elif job["kind"] == "pipeline":
            _run_pipeline(job_id, job["name"], params)
        else:
            raise ValueError(f"unknown job kind {job['kind']}")
    except subprocess.TimeoutExpired:
        db.execute("UPDATE job SET status='failed', error='timed out', "
                   "finished_at=datetime('now') WHERE id=?", (job_id,))


def _finish_skill(job_id, module, params, envelope, logs, rc) -> None:
    ok = rc == 0 and envelope is not None and envelope.get("results") is not None
    err = None
    if not ok:
        err = (envelope or {}).get("summary") or f"exit {rc}"
    artifacts = (envelope or {}).get("metadata", {}).get("artifacts", {})
    db.execute(
        "UPDATE job SET status=?, envelope_json=?, artifacts_json=?, logs_text=?, "
        "error=?, run_id=?, finished_at=datetime('now') WHERE id=?",
        ("succeeded" if ok else "failed", json.dumps(envelope or {}),
         json.dumps(artifacts), logs[-20000:], err,
         params.get("run_id"), job_id))
    if ok:
        _persist_outputs(job_id, module, params, envelope)


def _run_pipeline(job_id, key, params) -> None:
    pipe = PIPELINES.get(key)
    if not pipe:
        raise ValueError(f"unknown pipeline {key}")
    ctx: dict[str, Any] = dict(params)  # seed ctx with caller params (e.g. workbook path)
    log_acc: list[str] = []
    for step in pipe.steps:
        step_params = step.build(ctx)
        envelope, logs, rc = run_skill(step.module, step_params)
        log_acc.append(f"### step: {step.name}\n{logs}")
        db.execute("UPDATE job SET logs_text=? WHERE id=?",
                   ("\n\n".join(log_acc)[-40000:], job_id))
        ok = rc == 0 and envelope is not None and envelope.get("results") is not None
        if not ok:
            msg = (envelope or {}).get("summary") or f"step {step.name} exit {rc}"
            db.execute("UPDATE job SET status='failed', error=?, "
                       "finished_at=datetime('now') WHERE id=?", (msg, job_id))
            return
        step.capture(ctx, envelope)
        _persist_outputs(job_id, step.module, step_params, envelope)
    db.execute("UPDATE job SET status='succeeded', run_id=?, "
               "finished_at=datetime('now') WHERE id=?",
               (ctx.get("run_id"), job_id))
    # Domain hook: persist the MMM model snapshot for fast dashboard reads.
    if key == "rebuild_mmm":
        try:
            from .services import mmm_service
            mmm_service.persist_model()
        except Exception:
            pass


def _persist_outputs(job_id, module, params, envelope) -> None:
    """Record datasets (raw parquet pulls) and analytical results into SQLite."""
    artifacts = (envelope or {}).get("metadata", {}).get("artifacts", {}) or {}
    summary = (envelope or {}).get("summary")
    results = (envelope or {}).get("results")
    parquet = artifacts.get("parquet")
    # Raw data pulls -> dataset rows
    if parquet and (".hubspot." in module or "load_spend" in module):
        entity = ("deals" if "deals" in module else
                  "contacts" if "contacts" in module else
                  "spend" if "load_spend" in module else
                  "engagements" if "engagements" in module else module.split(".")[-1])
        nrec = (envelope or {}).get("metadata", {}).get("n_records")
        source = "hubspot" if ".hubspot." in module else "workbook"
        db.execute("INSERT INTO dataset (entity, source, parquet_path, n_records, "
                   "params_json, job_id) VALUES (?,?,?,?,?,?)",
                   (entity, source, parquet, nrec, json.dumps(params), job_id))
    # Analytical skill results -> result_artifact rows. Exclude prep + MMM skills
    # (feature engineering has no result; the MMM has its own dashboard).
    json_path = artifacts.get("json")
    _audit_excluded = {"skills.python.py_feature_engineering",
                       "skills.python.py_mmm_features",
                       "skills.python.py_marketing_mix_model"}
    if module.startswith("skills.python.py_") and module not in _audit_excluded:
        skill_name = module.split(".")[-1]
        db.execute("INSERT INTO result_artifact (run_id, skill_name, result_json, "
                   "summary, parquet_path, result_path, job_id) VALUES (?,?,?,?,?,?,?)",
                   (params.get("run_id"), skill_name,
                    json.dumps(results) if results is not None else None,
                    summary, parquet, json_path, job_id))
