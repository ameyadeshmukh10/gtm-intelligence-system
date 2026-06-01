"""py_trend_analysis — monthly conversion rate + Spearman vs time index.

Auto-detects the date column (first datetime column whose name contains
"create" / "conversion" / "first_conv" unless 'date_column' is explicit).
Flags recency bias when the latest month has incomplete data.

Params:
  run_id:       str
  target:       str
  date_column:  str    default = auto-detect
  p_cut:        float  default 0.05
"""
from __future__ import annotations

import pandas as pd
from scipy import stats

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run


def _auto_date_col(df, manifest) -> str | None:
    preferred = ["createdate", "first_conversion_date",
                 "recent_conversion_date", "hs_lastmodifieddate"]
    for p in preferred:
        if p in df.columns and pd.api.types.is_datetime64_any_dtype(df[p]):
            return p
    for c in manifest.get("date", []):
        if c in df.columns:
            return c
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    return None


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

        p_cut = p.get("p_cut", 0.05)
        work = df[[date_col, target]].dropna(subset=[date_col]).copy()
        work["_month"] = work[date_col].dt.to_period("M").astype(str)
        monthly = work.groupby("_month").agg(
            n=(target, "size"), conv=(target, "sum"))
        monthly["rate"] = monthly["conv"] / monthly["n"]
        monthly = monthly.reset_index()
        monthly["month_idx"] = range(len(monthly))

        warnings = []
        # Recency bias warning
        if len(monthly) >= 2:
            latest = monthly.iloc[-1]
            prev_avg = monthly.iloc[:-1]["n"].mean()
            if latest["n"] < 0.5 * prev_avg:
                warnings.append(
                    f"Latest month ({latest['_month']}) has n={int(latest['n'])} "
                    f"vs prior avg {prev_avg:.0f}. Likely incomplete — interpret "
                    f"recent rates with caution.")

        if len(monthly) < 3:
            emit({
                "results": {"monthly": monthly.to_dict(orient="records")},
                "summary": "Not enough months for trend detection.",
                "metadata": {"n_records": int(len(df)), "n_features": 1,
                             "warnings": warnings + ["<3 months of data"],
                             "artifacts": {}},
            })
            return

        r, pv = stats.spearmanr(monthly["month_idx"], monthly["rate"])
        r = float(r); pv = float(pv)
        meaningful = abs(r) >= 0.3 and pv < 0.1
        direction = "up" if r > 0 else ("down" if r < 0 else "flat")

        out = results_path(p["run_id"], "trend_analysis")
        write_json(out, {
            "target": target, "date_column": date_col,
            "monthly": monthly.to_dict(orient="records"),
            "spearman_r": r, "p_value": pv,
            "direction": direction, "meaningful": meaningful,
        })

        if not meaningful:
            warnings.append(
                f"Spearman r={r:.2f} (p={pv:.3g}) — no meaningful trend detected.")

        emit({
            "results": {
                "direction": direction,
                "spearman_r": r, "p_value": pv,
                "meaningful": meaningful,
                "monthly": monthly.to_dict(orient="records"),
            },
            "summary": (
                f"Trend on {date_col}: direction={direction}, "
                f"Spearman r={r:.2f}, p={pv:.3g}. "
                f"{'Meaningful' if meaningful else 'Not meaningful'} "
                f"across {len(monthly)} months."),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(monthly),
                         "warnings": warnings,
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
