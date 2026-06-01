"""py_combination_analysis — pairwise + triple combination conversion rates.

Auto-detects binary columns + rollups from the manifest, or takes explicit list.

Params:
  run_id:    str
  target:    str
  columns:   list[str]   default = binary + rollup from manifest
  min_n:     int         default 5
  top_pairs: int         default 25
  top_triples: int       default 15
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, binary_columns


def _group_rate(df: pd.DataFrame, target: str, cols: list[str]) -> tuple[int, float]:
    mask = np.ones(len(df), dtype=bool)
    for c in cols:
        mask &= (df[c] == 1)
    sub = df.loc[mask, target]
    if len(sub) == 0: return 0, 0.0
    return int(len(sub)), float(sub.mean())


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        cols = p.get("columns") or binary_columns(manifest)
        cols = [c for c in cols if c in df.columns and df[c].sum() >= p.get("min_n", 5)]
        min_n = p.get("min_n", 5)
        baseline = float(df[target].mean())

        pair_rows = []
        for a, b in combinations(cols, 2):
            n, rate = _group_rate(df, target, [a, b])
            if n >= min_n:
                pair_rows.append({
                    "combination": [a, b], "n": n, "rate": rate,
                    "lift_vs_baseline": rate - baseline,
                })
        pair_rows.sort(key=lambda r: r["rate"], reverse=True)

        triple_rows = []
        if p.get("triples", True) and len(cols) >= 3:
            # Cap search for perf: use top-50 cols by sum
            sums = df[cols].sum().sort_values(ascending=False)
            top_cols = sums.head(40).index.tolist()
            for a, b, c in combinations(top_cols, 3):
                n, rate = _group_rate(df, target, [a, b, c])
                if n >= min_n:
                    triple_rows.append({
                        "combination": [a, b, c], "n": n, "rate": rate,
                        "lift_vs_baseline": rate - baseline,
                    })
            triple_rows.sort(key=lambda r: r["rate"], reverse=True)

        top_pairs = p.get("top_pairs", 25)
        top_triples = p.get("top_triples", 15)

        out = results_path(p["run_id"], "combination_analysis")
        write_json(out, {
            "baseline": baseline,
            "pairs": pair_rows,
            "triples": triple_rows,
        })

        emit({
            "results": {
                "baseline": baseline,
                "top_pairs": pair_rows[:top_pairs],
                "top_triples": triple_rows[:top_triples],
                "n_pair_combos": len(pair_rows),
                "n_triple_combos": len(triple_rows),
            },
            "summary": (
                f"Evaluated {len(pair_rows)} pair and {len(triple_rows)} triple "
                f"combinations with n>={min_n}. Baseline={baseline:.2%}. "
                f"Full results at {out}."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(cols),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
