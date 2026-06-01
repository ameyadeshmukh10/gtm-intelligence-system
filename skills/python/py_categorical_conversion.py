"""py_categorical_conversion — per-category conversion rates with chi-square.

Auto-detects categorical columns from the manifest OR takes an explicit list.
Applies min_n threshold (default 5). Reports lift vs overall baseline.
Flags n < 15 as directional only.

Params:
  run_id:   str
  target:   str (override)
  columns:  list[str]   default = manifest categorical + binary + rollup
  min_n:    int         default 5
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
from scipy import stats

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, categorical_columns, binary_columns


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        min_n = p.get("min_n", 5)
        cols = p.get("columns") \
            or (categorical_columns(manifest) + binary_columns(manifest))
        cols = [c for c in cols if c in df.columns]

        baseline = float(df[target].mean())
        all_rows: list[dict] = []

        for c in cols:
            col_data = df[[c, target]].dropna(subset=[c])
            if col_data.empty: continue
            grp = col_data.groupby(c).agg(
                n=(target, "size"), conv=(target, "sum"))
            grp = grp[grp["n"] >= min_n].copy()
            if grp.empty: continue
            grp["rate"] = grp["conv"] / grp["n"]
            grp["lift_vs_baseline"] = grp["rate"] - baseline
            grp["lift_ratio"] = (grp["rate"] / baseline) if baseline > 0 else np.nan

            # Chi-square on contingency table (category x target)
            try:
                tbl = pd.crosstab(col_data[c], col_data[target])
                tbl = tbl.loc[grp.index]
                if tbl.shape[0] >= 2 and tbl.shape[1] >= 2:
                    chi2, pv, _, _ = stats.chi2_contingency(tbl)
                else:
                    chi2, pv = float("nan"), float("nan")
            except Exception:
                chi2, pv = float("nan"), float("nan")

            for idx, r in grp.sort_values("rate", ascending=False).iterrows():
                all_rows.append({
                    "column": c,
                    "category": str(idx),
                    "n": int(r["n"]),
                    "converted": int(r["conv"]),
                    "rate": float(r["rate"]),
                    "lift_vs_baseline": float(r["lift_vs_baseline"]),
                    "lift_ratio": (float(r["lift_ratio"])
                                   if not math.isnan(r["lift_ratio"]) else None),
                    "chi2_column": float(chi2) if not math.isnan(chi2) else None,
                    "p_value_column": float(pv) if not math.isnan(pv) else None,
                    "directional_only": bool(r["n"] < 15),
                })

        out = results_path(p["run_id"], "categorical_conversion")
        write_json(out, {"target": target, "baseline": baseline, "rows": all_rows})

        # Top segments by lift (strongest positive) and suppressors
        top_pos = sorted([r for r in all_rows if r["n"] >= 15],
                         key=lambda r: r["lift_vs_baseline"], reverse=True)[:10]
        top_neg = sorted([r for r in all_rows if r["n"] >= 15],
                         key=lambda r: r["lift_vs_baseline"])[:10]
        emit({
            "results": {
                "baseline": baseline,
                "n_rows": len(all_rows),
                "top_positive_segments": top_pos,
                "top_negative_segments": top_neg,
            },
            "summary": (
                f"Scored {len(all_rows)} segment rows across {len(cols)} columns. "
                f"Baseline rate = {baseline:.2%}. Full results at {out}."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(cols),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
