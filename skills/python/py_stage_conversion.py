"""py_stage_conversion — conversion rates + velocity between pipeline stages.

Single-pair mode:
  {"run_id": "...", "source_stage_date": "hs_date_entered_s0",
   "destination_stage_date": "hs_date_entered_s1"}

Full-matrix mode:
  {"run_id": "...", "auto_stage_matrix": true}
    auto-detects every column matching "hs_date_entered_*" or "_stage_date"
    and computes the full pairwise conversion + median days-in-stage matrix.

Drops records where destination date precedes source date.
"""
from __future__ import annotations

import re
from itertools import combinations

import numpy as np
import pandas as pd

from ..common.io_contract import (emit, emit_error, read_params,
                                   results_path, write_json)
from ._shared import load_run


def _detect_stage_cols(df: pd.DataFrame) -> list[str]:
    pat = re.compile(
        r"(hs_date_entered_.+|hs_v2_date_entered_.+|.+_stage_date|.+_entered_at)",
        re.I)
    return [c for c in df.columns if pat.match(c)
            and pd.api.types.is_datetime64_any_dtype(df[c])]


def _pair_conversion(df: pd.DataFrame, src: str, dst: str) -> dict:
    sub = df[[src, dst]].copy()
    entered_src = sub[src].notna()
    n_src = int(entered_src.sum())
    if n_src == 0:
        return {"source": src, "destination": dst,
                "n_source": 0, "n_destination": 0, "conversion_rate": None,
                "bad_dates_dropped": 0, "median_days_in_stage": None}
    entered_dst = sub[dst].notna()
    bad = entered_dst & entered_src & (sub[dst] < sub[src])
    valid = sub[entered_src & ~bad]
    converted = valid[dst].notna()
    rate = float(converted.mean()) if len(valid) else None
    days = None
    if converted.any():
        days = (valid.loc[converted, dst] - valid.loc[converted, src]).dt.days
        days = float(np.median(days.dropna())) if len(days.dropna()) else None
    return {
        "source": src, "destination": dst,
        "n_source": n_src,
        "n_destination": int(converted.sum()),
        "conversion_rate": rate,
        "bad_dates_dropped": int(bad.sum()),
        "median_days_in_stage": days,
    }


def main():
    try:
        p = read_params()
        df, manifest, _ = load_run(p)
        rows: list[dict] = []
        warnings = []

        if p.get("auto_stage_matrix"):
            stage_cols = _detect_stage_cols(df)
            if not stage_cols:
                emit_error("No stage-entry date columns detected")
                return
            # Order by median date
            order_key = [(c, df[c].dropna().median()) for c in stage_cols
                         if df[c].notna().any()]
            order_key.sort(key=lambda x: x[1])
            ordered = [c for c, _ in order_key]
            # Adjacent pairs
            for src, dst in zip(ordered, ordered[1:]):
                rows.append(_pair_conversion(df, src, dst))
        else:
            src = p.get("source_stage_date"); dst = p.get("destination_stage_date")
            if not src or not dst:
                emit_error("source_stage_date + destination_stage_date required "
                           "(or pass auto_stage_matrix=true)")
                return
            rows.append(_pair_conversion(df, src, dst))

        total_bad = sum(r["bad_dates_dropped"] for r in rows)
        if total_bad:
            warnings.append(
                f"Dropped {total_bad} records with destination date < source date.")

        out = results_path(p["run_id"], "stage_conversion")
        write_json(out, {"rows": rows})
        emit({
            "results": {"stages": rows},
            "summary": (
                f"Computed {len(rows)} stage transitions. "
                + ("Full chain: " + " -> ".join(
                    f"{r['source'].split('_')[-1]}->{r['destination'].split('_')[-1]} "
                    f"{(r['conversion_rate'] or 0):.1%}"
                    for r in rows) if p.get("auto_stage_matrix") else
                   f"{rows[0]['source']} -> {rows[0]['destination']}: "
                   f"{(rows[0]['conversion_rate'] or 0):.1%}")),
            "metadata": {"n_records": int(len(df)),
                         "n_features": len(rows),
                         "warnings": warnings,
                         "artifacts": {"json": str(out)}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
