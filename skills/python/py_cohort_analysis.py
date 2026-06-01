"""py_cohort_analysis — early/mid/late cohort comparison.

Auto-computes tercile date boundaries if none provided. Returns
conversion rate, feature distribution shift, and program prevalence
per cohort, plus chi-square test across cohorts.

Params:
  run_id:      str
  target:      str
  date_column: str   default = auto-detect (like py_trend_analysis)
  boundaries:  list[str]  optional — ISO dates, two values to split into
                          3 cohorts. Omit for auto-tercile.
  feature_cols: list[str] cols to profile per cohort (default: manifest
                          categorical + top binary rollups)
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from scipy import stats

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run, categorical_columns, binary_columns
from .py_trend_analysis import _auto_date_col


def main():
    try:
        p = read_params()
        df, manifest, target = load_run(p)
        date_col = p.get("date_column") or _auto_date_col(df, manifest)
        if not date_col:
            emit_error("No datetime column found — pass date_column explicitly")
            return
        if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce", utc=True)

        sub = df[df[date_col].notna()].copy()
        if p.get("boundaries"):
            b = [pd.to_datetime(x, utc=True) for x in p["boundaries"]]
            assert len(b) == 2
            labels = [f"early (≤{b[0].date()})",
                      f"mid ({b[0].date()}..{b[1].date()})",
                      f"late (>{b[1].date()})"]
            sub["_cohort"] = np.where(sub[date_col] <= b[0], labels[0],
                              np.where(sub[date_col] <= b[1], labels[1], labels[2]))
        else:
            q1 = sub[date_col].quantile(1/3)
            q2 = sub[date_col].quantile(2/3)
            labels = [f"early (<={q1.date()})",
                      f"mid ({q1.date()}..{q2.date()})",
                      f"late (>{q2.date()})"]
            sub["_cohort"] = np.where(sub[date_col] <= q1, labels[0],
                              np.where(sub[date_col] <= q2, labels[1], labels[2]))

        # Per-cohort conversion rate
        cohort_tbl = sub.groupby("_cohort").agg(
            n=(target, "size"),
            conv=(target, "sum"),
            rate=(target, "mean"),
        ).reindex(labels).reset_index()

        # Chi-square across cohorts
        ct = pd.crosstab(sub["_cohort"], sub[target]).reindex(labels)
        try:
            chi2, pv, _, _ = stats.chi2_contingency(ct.dropna())
        except Exception:
            chi2, pv = float("nan"), float("nan")

        # Feature profile per cohort
        feat_cols = p.get("feature_cols") \
            or (categorical_columns(manifest)[:10]
                + [c for c in binary_columns(manifest) if c in sub.columns][:15])
        profiles = {}
        for fc in feat_cols:
            if fc not in sub.columns: continue
            if sub[fc].dtype == object:
                top_val = sub.groupby("_cohort")[fc].agg(
                    lambda s: s.value_counts().head(3).to_dict())
                profiles[fc] = {k: v for k, v in top_val.items()}
            else:
                profiles[fc] = sub.groupby("_cohort")[fc].mean().to_dict()

        out = results_path(p["run_id"], "cohort_analysis")
        write_json(out, {
            "target": target, "date_column": date_col,
            "cohort_table": cohort_tbl.to_dict(orient="records"),
            "chi2": float(chi2) if not np.isnan(chi2) else None,
            "p_value": float(pv) if not np.isnan(pv) else None,
            "profiles": {k: {str(kk): vv for kk, vv in v.items()}
                         for k, v in profiles.items()},
        })

        emit({
            "results": {
                "cohorts": cohort_tbl.to_dict(orient="records"),
                "chi2": float(chi2) if not np.isnan(chi2) else None,
                "p_value": float(pv) if not np.isnan(pv) else None,
                "profile_sample": {k: v for i, (k, v) in enumerate(profiles.items())
                                   if i < 8},
            },
            "summary": (
                f"3 cohorts by {date_col}. Rates: "
                + ", ".join(f"{r['_cohort'].split('(')[0].strip()}={(r['rate'] or 0):.2%}"
                            for _, r in cohort_tbl.iterrows())
                + f". Chi² p={pv:.3g}" if not np.isnan(pv) else ""),
            "metadata": {"n_records": int(len(sub)),
                         "n_features": len(feat_cols),
                         "warnings": [],
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
