"""Marketing Mix Model: load the canonical result, persist a snapshot, and run
deterministic what-if projections off the fitted response curves (no re-fit).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..config import RESULTS_DIR, FEATURES_DIR, CANONICAL_MMM_RUN_ID


def result_path(run_id: str = CANONICAL_MMM_RUN_ID) -> Path:
    return RESULTS_DIR / f"{run_id}_marketing_mix_model.json"


def load_result(run_id: str = CANONICAL_MMM_RUN_ID) -> dict | None:
    p = result_path(run_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def load_timeseries(run_id: str = CANONICAL_MMM_RUN_ID) -> dict | None:
    """The period x (y, spend_*) design matrix, for the spend-vs-deals timeline."""
    fp = FEATURES_DIR / f"{run_id}.parquet"
    if not fp.exists():
        return None
    import pandas as pd
    df = pd.read_parquet(fp)
    df = df.sort_values("period")
    spend_cols = [c for c in df.columns if c.startswith("spend_")]
    return {
        "period": [str(p)[:10] for p in df["period"]],
        "y": [float(v) for v in df["y"]],
        "spend_total": [float(v) for v in df[spend_cols].sum(axis=1)] if spend_cols else [],
        "spend_by_channel": {c.replace("spend_", ""): [float(v) for v in df[c]]
                             for c in spend_cols},
    }


def persist_model(run_id: str = CANONICAL_MMM_RUN_ID) -> int | None:
    """Snapshot the result JSON into the mmm_model table for fast dashboard reads."""
    res = load_result(run_id)
    if not res:
        return None
    return db.execute(
        "INSERT INTO mmm_model (run_id, granularity, n_periods, channel_groups_json, "
        "baseline_json, incremental_json, channels_json, diagnostics_json, result_path) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, res.get("granularity"), res.get("n_periods"),
         json.dumps([c["channel"] for c in res.get("channels", [])]),
         json.dumps(res.get("baseline")), json.dumps(res.get("incremental")),
         json.dumps(res.get("channels")), json.dumps(res.get("diagnostics")),
         str(result_path(run_id))))


def latest_snapshot(run_id: str = CANONICAL_MMM_RUN_ID) -> dict | None:
    return db.query_one("SELECT * FROM mmm_model WHERE run_id=? "
                        "ORDER BY id DESC LIMIT 1", (run_id,))


# --------------------------------------------------------------- what-if planner
def _interp_curve(curve: list[dict], spend: float) -> float:
    """Piecewise-linear interpolation of contribution at an absolute spend level.

    `curve` is the model's response_curve: points of {spend, contribution},
    monotonic in spend. Beyond the last point we hold the saturated value
    (extrapolating a saturation curve upward would overstate returns).
    """
    pts = sorted(curve, key=lambda p: p["spend"])
    if not pts:
        return 0.0
    if spend <= pts[0]["spend"]:
        return float(pts[0]["contribution"])
    if spend >= pts[-1]["spend"]:
        return float(pts[-1]["contribution"])
    for a, b in zip(pts, pts[1:]):
        if a["spend"] <= spend <= b["spend"]:
            span = b["spend"] - a["spend"]
            if span <= 0:
                return float(b["contribution"])
            frac = (spend - a["spend"]) / span
            return float(a["contribution"] + frac * (b["contribution"] - a["contribution"]))
    return float(pts[-1]["contribution"])


def whatif(spend_plan: dict[str, float], run_id: str = CANONICAL_MMM_RUN_ID) -> dict[str, Any]:
    """Project deals for a planned per-channel spend by reading fitted curves.

    spend_plan: {channel: planned_absolute_spend}. Channels omitted keep current
    spend. Returns per-channel projected contribution + a directional CI band
    (scaled from each channel's contribution CI) and totals incl. baseline.
    """
    res = load_result(run_id)
    if not res:
        return {"error": "no MMM result available"}
    baseline = res.get("baseline", {}).get("mean", 0.0)
    rows, proj_total, lo_total, hi_total = [], 0.0, 0.0, 0.0
    for ch in res.get("channels", []):
        name = ch["channel"]
        cur_spend = float(ch.get("total_spend", 0.0))
        planned = float(spend_plan.get(name, cur_spend))
        cur_contrib = float(ch.get("contribution_mean", 0.0))
        proj_contrib = _interp_curve(ch.get("response_curve", []), planned)
        # Directional CI: scale the channel's contribution CI by proj/current ratio.
        ci = ch.get("contribution_ci90", [0.0, 0.0])
        ratio = (proj_contrib / cur_contrib) if cur_contrib > 1e-9 else 1.0
        lo, hi = float(ci[0]) * ratio, float(ci[1]) * ratio
        rows.append({
            "channel": name, "current_spend": cur_spend, "planned_spend": planned,
            "current_contribution": cur_contrib, "projected_contribution": proj_contrib,
            "delta": proj_contrib - cur_contrib, "ci90": [lo, hi],
        })
        proj_total += proj_contrib
        lo_total += lo
        hi_total += hi
    return {
        "run_id": run_id,
        "baseline": baseline,
        "channels": rows,
        "projected_incremental": proj_total,
        "projected_incremental_ci90": [lo_total, hi_total],
        "projected_total_deals": baseline + proj_total,
        "current_incremental": res.get("incremental", {}).get("mean", 0.0),
        "current_total_deals": res.get("y_total", baseline),
    }
