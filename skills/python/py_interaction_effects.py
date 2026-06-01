"""py_interaction_effects — 2x2 interaction tables + SYNERGY/SUPPRESSION flags.

For each pair (a, b):
  - observed rate when both=1
  - additive-expected rate = min(1, rate(a=1) + rate(b=1) - baseline)
  - delta = observed - additive-expected
  - SYNERGY flag if delta > +10pp ; SUPPRESSION flag if delta < -5pp

Params:
  run_id: str
  target: str
  pairs:  list[[str,str]]  explicit pairs (preferred)
                           OR auto-derive top combos from _shared.binary_columns
  min_n:  int              default 5
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, binary_columns


def _rate(df, target, cond):
    sub = df.loc[cond, target]
    return float(sub.mean()) if len(sub) else None, int(len(sub))


def _pair_effect(df, target, a, b, baseline):
    both = (df[a] == 1) & (df[b] == 1)
    only_a = (df[a] == 1) & (df[b] == 0)
    only_b = (df[a] == 0) & (df[b] == 1)
    neither = (df[a] == 0) & (df[b] == 0)
    r_both, n_both = _rate(df, target, both)
    r_a, n_a = _rate(df, target, (df[a] == 1))
    r_b, n_b = _rate(df, target, (df[b] == 1))
    if r_both is None or r_a is None or r_b is None:
        return None
    expected = min(1.0, r_a + r_b - baseline)
    delta = r_both - expected
    flag = "SYNERGY" if delta >= 0.10 else ("SUPPRESSION" if delta <= -0.05 else None)
    return {
        "a": a, "b": b, "baseline": baseline,
        "n_both": n_both, "rate_both": r_both,
        "n_a": n_a, "rate_a": r_a,
        "n_b": n_b, "rate_b": r_b,
        "n_only_a": int(only_a.sum()),
        "rate_only_a": (float(df.loc[only_a, target].mean())
                        if only_a.any() else None),
        "n_only_b": int(only_b.sum()),
        "rate_only_b": (float(df.loc[only_b, target].mean())
                        if only_b.any() else None),
        "n_neither": int(neither.sum()),
        "rate_neither": (float(df.loc[neither, target].mean())
                         if neither.any() else None),
        "additive_expected": expected,
        "delta_vs_additive": delta,
        "flag": flag,
    }


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        baseline = float(df[target].mean())
        min_n = p.get("min_n", 5)

        pairs: list[tuple[str, str]]
        if p.get("pairs"):
            pairs = [tuple(x) for x in p["pairs"]]
        else:
            cols = [c for c in binary_columns(manifest)
                    if c in df.columns and df[c].sum() >= min_n]
            # Cap combinatorial explosion
            sums = df[cols].sum().sort_values(ascending=False)
            top = sums.head(30).index.tolist()
            pairs = list(combinations(top, 2))

        results = []
        synergies = []
        suppressions = []
        for a, b in pairs:
            if a not in df.columns or b not in df.columns: continue
            res = _pair_effect(df, target, a, b, baseline)
            if res is None or res["n_both"] < min_n: continue
            results.append(res)
            if res["flag"] == "SYNERGY": synergies.append(res)
            elif res["flag"] == "SUPPRESSION": suppressions.append(res)

        synergies.sort(key=lambda r: r["delta_vs_additive"], reverse=True)
        suppressions.sort(key=lambda r: r["delta_vs_additive"])

        out = results_path(p["run_id"], "interaction_effects")
        write_json(out, {"baseline": baseline, "all": results,
                         "synergies": synergies,
                         "suppressions": suppressions})
        emit({
            "results": {
                "baseline": baseline,
                "n_pairs_tested": len(results),
                "synergies": synergies[:15],
                "suppressions": suppressions[:15],
            },
            "summary": (
                f"Tested {len(results)} pairs. Found {len(synergies)} SYNERGY "
                f"and {len(suppressions)} SUPPRESSION flags."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(results),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
