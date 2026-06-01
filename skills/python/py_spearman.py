"""py_spearman — rank correlation (single-pair or batch vs a reference column).

Single-pair:
  {"run_id": "...", "x": "col_a", "y": "col_b"}

Batch (all numeric features vs a reference):
  {"run_id": "...", "reference": "days_to_next_stage"}
"""
from __future__ import annotations

from scipy import stats

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, numeric_columns


def _corr(a, b):
    a = a.dropna(); b = b.dropna()
    idx = a.index.intersection(b.index)
    if len(idx) < 5: return None
    r, pv = stats.spearmanr(a.loc[idx], b.loc[idx])
    return {"n": int(len(idx)), "r": float(r), "p_value": float(pv)}


def main():
    try:
        p = read_params()
        df, manifest, _ = load_run(p)

        if p.get("x") and p.get("y"):
            res = _corr(df[p["x"]], df[p["y"]])
            summary = (f"Spearman {p['x']} vs {p['y']}: "
                       f"r={res['r']:.3f}, p={res['p_value']:.3g}, n={res['n']}"
                       if res else "Insufficient data for Spearman")
            out = results_path(p["run_id"],
                               f"spearman_{p['x']}_vs_{p['y']}")
            write_json(out, {"x": p["x"], "y": p["y"], "result": res})
            emit({"results": {p["x"]: res}, "summary": summary,
                  "metadata": {"n_records": int(len(df)), "n_features": 1,
                               "warnings": [],
                               "artifacts": {"json": str(out)}}})
            return

        ref = p.get("reference")
        if not ref or ref not in df.columns:
            emit_error("reference column required for batch mode")
            return
        cols = p.get("features") or numeric_columns(manifest)
        cols = [c for c in cols if c in df.columns and c != ref]
        rows = []
        for c in cols:
            r = _corr(df[c], df[ref])
            if r:
                rows.append({"feature": c, **r})
        rows.sort(key=lambda x: abs(x["r"]), reverse=True)
        out = results_path(p["run_id"], f"spearman_batch_{ref}")
        write_json(out, {"reference": ref, "rows": rows})

        sig = [r for r in rows if r["p_value"] < 0.05 and abs(r["r"]) >= 0.3]
        emit({
            "results": {"reference": ref, "top": rows[:15],
                        "n_significant": len(sig)},
            "summary": (
                f"Spearman batch vs {ref}: {len(rows)} tested, "
                f"{len(sig)} meaningful (|r|>=0.3, p<0.05)."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(rows),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
