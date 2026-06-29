"""py_mmm_features — build the time-series design matrix for the MMM.

Unlike py_feature_engineering (which builds a CONTACT-level matrix for
classification), this builds a PERIOD-level matrix for an aggregate time-series
regression:

  one row per period, with
    y              outcome      = deals/opps created that period (count)
    spend_<group>  media        = spend per channel group that period ($)
    trend          control      = standardized time index
    season_sin/cos control      = annual Fourier pair (seasonality)
    active_owners  control      = distinct deal owners active that period (sales capacity proxy)
    eng_volume     control      = engagement volume that period (optional)

Outcome is deals CREATED (not closed-won) — closest to marketing's causal reach
in B2B and avoids the 3-9mo sales-cycle lag confounding adstock.

Params (JSON):
  run_id:            str                 (required) label for this MMM run
  deals_input:       str                 (required) path to hs_pull_deals parquet
  spend_input:       str                 (required) path to load_spend parquet (long: period, channel_group, spend)
  engagements_input: str                 optional path to hs_pull_engagements parquet (capacity control)
  granularity:       "month" | "week"    default "month"
  date_column:       str                 default "createdate" (deal-create date)
  outcome:           "deals_created" | "pipeline_amount"   default "deals_created"
  deal_filter:       str                 optional pandas query to scope deals (e.g. "pipeline == 'default'")
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..common.io_contract import (DATA_DIR, emit, emit_error, features_path,
                                   read_params, write_json)


def _period_start(series: pd.Series, granularity: str) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)
    return dt.dt.to_period("W" if granularity == "week" else "M").dt.start_time


def _mmm_manifest_path(run_id: str):
    return DATA_DIR / "features" / f"{run_id}_mmm_manifest.json"


def main():
    try:
        p = read_params()
        run_id = p.get("run_id")
        if not run_id:
            emit_error("run_id is required")
            return
        for req in ("deals_input", "spend_input"):
            if not p.get(req):
                emit_error(f"{req} is required")
                return

        granularity = p.get("granularity", "month")
        date_col = p.get("date_column", "createdate")
        outcome = p.get("outcome", "deals_created")
        warnings: list[str] = []

        # ---- Outcome: deals created per period ----
        deals = pd.read_parquet(p["deals_input"])
        if p.get("deal_filter"):
            try:
                deals = deals.query(p["deal_filter"])
            except Exception as e:
                warnings.append(f"deal_filter failed ({e}); using all deals.")
        if date_col not in deals.columns:
            emit_error(f"date_column '{date_col}' not in deals columns")
            return
        deals["period"] = _period_start(deals[date_col], granularity)
        deals = deals.dropna(subset=["period"])

        if outcome == "pipeline_amount":
            deals["amount"] = pd.to_numeric(deals.get("amount"), errors="coerce").fillna(0.0)
            y = deals.groupby("period")["amount"].sum().rename("y")
        else:
            y = deals.groupby("period").size().rename("y")

        # Sales-capacity proxy from deals: distinct active owners per period
        cap = None
        if "hubspot_owner_id" in deals.columns:
            cap = deals.groupby("period")["hubspot_owner_id"].nunique().rename("active_owners")

        # ---- Media: spend per channel group, pivoted wide ----
        spend = pd.read_parquet(p["spend_input"])
        need = {"period", "channel_group", "spend"}
        if not need.issubset(spend.columns):
            emit_error(f"spend_input must have columns {need}; got {list(spend.columns)}")
            return
        spend["period"] = _period_start(spend["period"], granularity)
        wide = (spend.pivot_table(index="period", columns="channel_group",
                                  values="spend", aggfunc="sum", fill_value=0.0))
        wide.columns = [f"spend_{c}" for c in wide.columns]
        media_cols = list(wide.columns)

        # ---- Assemble on a continuous period grid (no gaps) ----
        start = min(y.index.min(), wide.index.min())
        end = max(y.index.max(), wide.index.max())
        freq = "W-MON" if granularity == "week" else "MS"
        grid = pd.date_range(start=start, end=end, freq=freq)
        m = pd.DataFrame(index=grid)
        m.index.name = "period"
        m = m.join(y).join(wide)
        if cap is not None:
            m = m.join(cap)
        m["y"] = m["y"].fillna(0.0)
        for c in media_cols:
            m[c] = m[c].fillna(0.0)
        if "active_owners" in m.columns:
            m["active_owners"] = m["active_owners"].fillna(0.0)

        # Optional engagement-volume capacity control
        if p.get("engagements_input"):
            try:
                eng = pd.read_parquet(p["engagements_input"])
                ecol = next((c for c in ("hs_timestamp", "hs_createdate", "createdate")
                             if c in eng.columns), None)
                if ecol:
                    eng["period"] = _period_start(eng[ecol], granularity)
                    ev = eng.groupby("period").size().rename("eng_volume")
                    m = m.join(ev)
                    m["eng_volume"] = m["eng_volume"].fillna(0.0)
            except Exception as e:
                warnings.append(f"engagements_input ignored ({e})")

        # ---- Controls: trend + annual seasonality ----
        m = m.reset_index()
        n = len(m)
        m["trend"] = (np.arange(n) - (n - 1) / 2) / max(n - 1, 1)  # centered [-0.5,0.5]
        period_per_year = 52.0 if granularity == "week" else 12.0
        ang = 2 * np.pi * (np.arange(n) % period_per_year) / period_per_year
        m["season_sin"] = np.sin(ang)
        m["season_cos"] = np.cos(ang)

        control_cols = ["trend", "season_sin", "season_cos"]
        if "active_owners" in m.columns:
            control_cols.append("active_owners")
        if "eng_volume" in m.columns:
            control_cols.append("eng_volume")

        # ---- Feasibility guardrails (Part 4) ----
        n_media = len(media_cols)
        # ~3 estimated params per channel (decay, beta, half-saturation).
        params_est = 3 * n_media + len(control_cols) + 2  # +intercept,+dispersion
        if params_est > n:
            warnings.append(
                f"{params_est} estimated params vs {n} periods — UNDER-IDENTIFIED. "
                f"Merge channel groups (target <= {max((n - len(control_cols) - 2)//3,1)} "
                f"groups) or aggregate weekly to add observations.")
        if n < 12:
            warnings.append(f"Only {n} {granularity}s — below ~12 minimum; "
                            f"results are directional, not causal.")
        zero_media = [c for c in media_cols if (m[c] <= 0).mean() > 0.5]
        if zero_media:
            warnings.append(f"Sparse media columns (>50% zero): {zero_media} "
                            f"— may collapse to the prior.")

        out_path = features_path(run_id)
        m.to_parquet(out_path, index=False)

        manifest = {
            "run_id": run_id,
            "kind": "mmm",
            "granularity": granularity,
            "features_path": str(out_path),
            "date": ["period"],
            "outcome": "y",
            "outcome_kind": outcome,
            "media": media_cols,
            "media_groups": [c.replace("spend_", "") for c in media_cols],
            "control": control_cols,
            "n_periods": int(n),
        }
        write_json(_mmm_manifest_path(run_id), manifest)

        emit({
            "results": {
                "run_id": run_id,
                "granularity": granularity,
                "n_periods": int(n),
                "outcome": outcome,
                "media_groups": manifest["media_groups"],
                "controls": control_cols,
                "y_total": float(m["y"].sum()),
                "y_mean_per_period": float(m["y"].mean()),
                "spend_total_by_group": {c.replace("spend_", ""): float(m[c].sum())
                                         for c in media_cols},
            },
            "summary": (
                f"Built MMM matrix: {n} {granularity}s x {n_media} channel groups "
                f"(outcome={outcome}, total y={m['y'].sum():.0f}). "
                f"{'WARNINGS present — see metadata.' if warnings else 'No feasibility warnings.'}"),
            "metadata": {
                "n_records": int(n), "n_features": n_media + len(control_cols),
                "warnings": warnings,
                "artifacts": {"features": str(out_path),
                              "mmm_manifest": str(_mmm_manifest_path(run_id))},
            },
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
