"""py_mann_whitney — batch Mann-Whitney U across all numeric features.

Flags inverted medians (converted group has LOWER values than non-converted).
Interpretation: inverted medians on volume metrics indicate high-volume
programs reaching unqualified audiences.

Params:
  run_id:    str
  target:    str (override default from manifest)
  features:  list[str]  optional subset; defaults to manifest numeric cols
  p_cut:     float      default 0.05 for significance flag
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, numeric_columns


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        cols = p.get("features") or numeric_columns(manifest)
        p_cut = p.get("p_cut", 0.05)

        y = df[target].astype(int).values
        pos = df[target] == 1
        rows = []
        warnings = []
        for c in cols:
            if c not in df.columns: continue
            a = df.loc[pos, c].dropna().values
            b = df.loc[~pos, c].dropna().values
            if len(a) < 5 or len(b) < 5:
                continue
            try:
                u, pv = stats.mannwhitneyu(a, b, alternative="two-sided")
            except ValueError:
                continue
            med_pos = float(np.median(a)); med_neg = float(np.median(b))
            inverted = med_pos < med_neg
            rows.append({
                "feature": c,
                "u_stat": float(u), "p_value": float(pv),
                "median_converted": med_pos, "median_not": med_neg,
                "median_diff": med_pos - med_neg,
                "n_converted": int(len(a)), "n_not": int(len(b)),
                "significant": bool(pv < p_cut),
                "inverted_median": inverted,
            })
            if inverted and pv < p_cut and any(
                    k in c.lower() for k in
                    ("delivered", "sent", "sessions", "pageviews", "count")):
                warnings.append(
                    f"Inverted median on volume metric '{c}': converted median "
                    f"{med_pos:.1f} < non-converted {med_neg:.1f} (p={pv:.3g}). "
                    f"Likely mass-program reach to unqualified audience.")

        results = sorted(rows, key=lambda r: r["p_value"])
        sig = [r for r in results if r["significant"]]
        out = results_path(p["run_id"], "mann_whitney")
        write_json(out, {"target": target, "results": results})

        top = results[:10]
        emit({
            "results": {
                "n_features_tested": len(results),
                "n_significant": len(sig),
                "top_significant": [
                    {"feature": r["feature"], "p": r["p_value"],
                     "median_converted": r["median_converted"],
                     "median_not": r["median_not"],
                     "inverted": r["inverted_median"]}
                    for r in sig[:15]],
                "top_by_p_all": [
                    {"feature": r["feature"], "p": r["p_value"]}
                    for r in top],
            },
            "summary": (
                f"Tested {len(results)} numeric features vs {target}. "
                f"{len(sig)} significant at p<{p_cut}. "
                f"Full results at {out}."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(results),
                         "warnings": warnings,
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
