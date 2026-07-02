"""Marketing-Influence Cohort Report service — load/list saved runs and shape
the Plotly series for the /influence surface.

Results are the influence_report skill's on-disk JSON (data/results/<run_id>_influence_report.json),
indexed by the standard result_artifact table the job worker already populates.
"""
from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path

from .. import db
from ..config import RESULTS_DIR

_SUFFIX = "_influence_report.json"


def default_run_id(granularity: str, start: str, end: str) -> str:
    """Mirror skills.reports.influence_report._default_run_id so the router and
    skill agree on the run_id (keeps jobs + saved runs addressable)."""
    digits = lambda s: re.sub(r"[^0-9]", "", s or "")[:8]
    return f"influence_{granularity}_{digits(start)}_{digits(end)}"


def result_path(run_id: str) -> Path:
    return RESULTS_DIR / f"{run_id}{_SUFFIX}"


def load_result(run_id: str) -> dict | None:
    p = result_path(run_id)
    if p.exists():
        return json.loads(p.read_text())
    row = db.query_one("SELECT result_json FROM result_artifact WHERE run_id=? "
                       "AND skill_name='influence_report' ORDER BY id DESC LIMIT 1",
                       (run_id,))
    return db.loads(row["result_json"]) if row else None


def list_runs() -> list[dict]:
    """Every saved influence run (disk ∪ DB), newest first."""
    seen, out = set(), []
    for p in sorted(glob.glob(str(RESULTS_DIR / f"*{_SUFFIX}")),
                    key=os.path.getmtime, reverse=True):
        run_id = Path(p).name[:-len(_SUFFIX)]
        if run_id in seen:
            continue
        seen.add(run_id)
        try:
            res = json.loads(Path(p).read_text())
        except Exception:
            continue
        w = res.get("window", {})
        t = res.get("totals", {})
        out.append({
            "run_id": run_id,
            "start": w.get("start"), "end": w.get("end"),
            "granularity": w.get("granularity"),
            "n_deals": t.get("n_deals"),
            "pct_deals_influenced": t.get("pct_deals_influenced"),
        })
    return out


def latest() -> dict | None:
    runs = list_runs()
    return load_result(runs[0]["run_id"]) if runs else None


def to_charts(res: dict) -> dict:
    """Shape the report into Plotly-ready series for the template."""
    periods = res.get("periods", [])
    return {
        "periods": [p["period"] for p in periods],
        "influenced_pipeline": [p["influenced_pipeline_usd"] for p in periods],
        "cold_pipeline": [p["cold_pipeline_usd"] for p in periods],
        "influence_rate_deals": [round(p["influence_rate_deals"] * 100, 1) for p in periods],
        "influence_rate_pipeline": [round(p["influence_rate_pipeline"] * 100, 1) for p in periods],
        "sub_signals": [{"signal": s["signal"], "deals": s["deals"],
                         "pipeline": s["pipeline_usd"]} for s in res.get("sub_signals", [])],
    }
