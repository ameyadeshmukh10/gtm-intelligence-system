"""Generic renderers that shape each analytical skill's result JSON into a
normalized {title, note, table, chart} structure the audit templates render.

A skill the registry doesn't recognize falls back to a pretty-printed JSON dump,
so new skills still display (just without a bespoke chart).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import db
from ..config import RESULTS_DIR


def list_results() -> list[dict]:
    """Union of DB-recorded results and any result JSONs already on disk."""
    seen, out = set(), []
    for r in db.query("SELECT skill_name, run_id, summary, result_path, created_at "
                       "FROM result_artifact ORDER BY id DESC"):
        key = r.get("result_path") or f"{r['run_id']}_{r['skill_name']}"
        if key in seen:
            continue
        seen.add(key)
        out.append({**r, "source": "db"})
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if any((row.get("result_path") or "").endswith(p.name) for row in out):
            continue
        if p.name.endswith("_marketing_mix_model.json"):
            continue  # MMM has its own dashboard
        out.append({"skill_name": _skill_from_filename(p.name),
                    "run_id": _runid_from_filename(p.name), "summary": None,
                    "result_path": str(p), "created_at": None, "source": "disk"})
    return out


def _skill_from_filename(name: str) -> str:
    stem = name[:-5]
    for s in ("trend_analysis", "categorical_conversion", "random_forest",
              "kmeans_cluster", "cohort_analysis", "stage_conversion",
              "combination_analysis", "interaction_effects", "mann_whitney",
              "logistic_regression", "spearman"):
        if stem.endswith(s):
            return s
    return stem.split("_")[-1]


def _runid_from_filename(name: str) -> str:
    return name[:-5].split("_" + _skill_from_filename(name))[0]


def load_result(result_path: str | None, run_id: str | None,
                skill_name: str) -> dict | None:
    if result_path and Path(result_path).exists():
        return json.loads(Path(result_path).read_text())
    row = db.query_one("SELECT result_json FROM result_artifact WHERE run_id=? "
                       "AND skill_name=? ORDER BY id DESC LIMIT 1",
                       (run_id, skill_name))
    return db.loads(row["result_json"]) if row else None


# ----------------------------------------------------------------- renderers
def render(skill_name: str, res: dict) -> dict[str, Any]:
    fn = _RENDERERS.get(skill_name)
    if fn:
        try:
            return fn(res)
        except Exception as e:  # never let a shape mismatch break the page
            return _raw(res, note=f"(default view — renderer error: {e})")
    return _raw(res)


def _raw(res: dict, note: str = "") -> dict:
    return {"title": "Result", "note": note,
            "json": json.dumps(res, indent=2)[:20000], "table": None, "chart": None}


def _trend(res):
    monthly = res.get("monthly", [])
    return {
        "title": "Conversion trend",
        "note": f"Spearman r={res.get('spearman_r'):.2f}, p={res.get('p_value'):.3g}, "
                f"direction={res.get('direction')} "
                f"({'meaningful' if res.get('meaningful') else 'not meaningful'})",
        "table": {"columns": ["month", "n", "conv", "rate"],
                  "rows": [[m.get("_month"), m.get("n"), m.get("conv"),
                            round(m.get("rate", 0), 4)] for m in monthly]},
        "chart": {"type": "line", "x": [m.get("_month") for m in monthly],
                  "y": [m.get("rate") for m in monthly], "ylabel": "conversion rate"},
    }


def _categorical(res):
    rows = sorted(res.get("rows", []), key=lambda r: -(r.get("lift_ratio") or 0))
    return {
        "title": "Categorical conversion",
        "note": f"baseline rate {res.get('baseline'):.3%}",
        "table": {"columns": ["column", "category", "n", "rate", "lift_ratio", "p_value"],
                  "rows": [[r.get("column"), r.get("category"), r.get("n"),
                            round(r.get("rate", 0), 4), round(r.get("lift_ratio") or 0, 2),
                            round(r.get("p_value_column") or 1, 4)] for r in rows[:60]]},
        "chart": {"type": "bar",
                  "x": [f"{r.get('column')}={r.get('category')}" for r in rows[:15]],
                  "y": [r.get("lift_ratio") or 0 for r in rows[:15]], "ylabel": "lift ratio"},
    }


def _random_forest(res):
    imp = res.get("importances", [])
    return {
        "title": "Random forest feature importance",
        "note": f"AUC {res.get('auc'):.3f} · F1 {res.get('f1'):.3f} · {res.get('n_features')} features",
        "table": {"columns": ["feature", "importance"],
                  "rows": [[i.get("feature"), round(i.get("importance", 0), 4)] for i in imp]},
        "chart": {"type": "bar", "x": [i.get("feature") for i in imp[:15]],
                  "y": [i.get("importance") for i in imp[:15]], "ylabel": "importance",
                  "horizontal": True},
    }


def _kmeans(res):
    profs = res.get("profiles", [])
    return {
        "title": "KMeans clusters",
        "note": f"best k={res.get('best_k')} · overall rate {res.get('overall_rate'):.3%}",
        "table": {"columns": ["cluster", "n", "rate", "lift_vs_overall", "top_traits"],
                  "rows": [[p.get("cluster"), p.get("n"), round(p.get("rate", 0), 4),
                            round(p.get("lift_vs_overall", 0), 4),
                            ", ".join(t.get("feature", "") for t in p.get("top_defining_traits", [])[:3])]
                           for p in profs]},
        "chart": {"type": "bar", "x": [f"cluster {p.get('cluster')}" for p in profs],
                  "y": [p.get("rate") for p in profs], "ylabel": "conversion rate"},
    }


def _cohort(res):
    tbl = res.get("cohort_table", [])
    return {
        "title": "Cohort analysis",
        "note": f"chi2={res.get('chi2'):.1f}, p={res.get('p_value'):.2g}",
        "table": {"columns": ["cohort", "n", "conv", "rate"],
                  "rows": [[c.get("_cohort"), c.get("n"), c.get("conv"),
                            round(c.get("rate", 0), 4)] for c in tbl]},
        "chart": {"type": "bar", "x": [c.get("_cohort") for c in tbl],
                  "y": [c.get("rate") for c in tbl], "ylabel": "conversion rate"},
    }


def _stage(res):
    rows = res.get("rows", [])
    return {
        "title": "Stage conversion",
        "note": "stage-to-stage conversion + velocity",
        "table": {"columns": ["source", "destination", "n_source", "conversion_rate",
                              "median_days_in_stage"],
                  "rows": [[r.get("source"), r.get("destination"), r.get("n_source"),
                            round(r.get("conversion_rate", 0), 4),
                            r.get("median_days_in_stage")] for r in rows]},
        "chart": None,
    }


_RENDERERS = {
    "trend_analysis": _trend,
    "categorical_conversion": _categorical,
    "random_forest": _random_forest,
    "kmeans_cluster": _kmeans,
    "cohort_analysis": _cohort,
    "stage_conversion": _stage,
}
