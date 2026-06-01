"""py_feature_engineering — preprocess raw HubSpot pulls and emit column manifest.

Every downstream statistical skill reads the manifest this skill produces.
It handles deduplication, multi-value field parsing (semicolon-delimited),
ordinal encoding, binary flag creation, NaN imputation, date-derived
features, and contamination-column exclusion.

Params (JSON):
  input:        str | list[str]  path(s) to parquet files from hs_pull_*
  run_id:       str              unique label for this analysis run
  target:       str              name of the target column (e.g. "converted", "has_deal")
  target_rule:  dict             optional rule to *create* the target:
                                 {"expr": "num_associated_deals >= 1"}  OR
                                 {"from_date": "hs_date_entered_qualifiedtobuy",
                                  "name": "converted"}
  multi_value_fields: list[str]  columns to parse as semicolon-delimited lists
  exclude_values:     dict       {column: [values_to_drop_before_parsing]}
  dedupe_on:    list[str]        default ["id"]
  extra_excluded: list[str]      columns to blacklist in the manifest
  seniority_map: dict            custom ordinal map for seniority (optional)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..common.io_contract import (emit, emit_error, features_path,
                                   manifest_path, read_params, write_json)
from ..common.manifest import classify_columns


DEFAULT_SENIORITY_MAP = {
    "intern": 1, "entry": 1, "associate": 2, "analyst": 2,
    "senior": 3, "manager": 4, "lead": 4,
    "director": 5, "head": 5, "principal": 5,
    "vp": 6, "vice president": 6, "avp": 5,
    "svp": 7, "executive": 7, "chief": 8, "c-level": 8,
    "ceo": 8, "cto": 8, "cfo": 8, "cmo": 8, "coo": 8, "cro": 8,
}

REVENUE_RANGE_MAP = {
    "0-1m": 1, "1-10m": 2, "10-50m": 3, "50-100m": 4,
    "100-500m": 5, "500m-1b": 6, "1b+": 7, "1b-5b": 7, "5b+": 8,
}


def _load_inputs(input_param: Any) -> pd.DataFrame:
    paths = input_param if isinstance(input_param, list) else [input_param]
    frames = [pd.read_parquet(p) for p in paths]
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


def _ordinal_encode(series: pd.Series, mapping: dict) -> pd.Series:
    def lookup(v):
        if pd.isna(v): return np.nan
        s = str(v).strip().lower()
        for key, rank in mapping.items():
            if key in s:
                return rank
        return np.nan
    return series.map(lookup)


def _parse_multi(series: pd.Series, excluded_vals: set[str]) -> list[list[str]]:
    out = []
    for v in series.fillna(""):
        if not v:
            out.append([]); continue
        parts = [p.strip() for p in re.split(r"[;,|]", str(v)) if p.strip()]
        parts = [p for p in parts if p.lower() not in excluded_vals
                 and not any(junk in p.lower() for junk in
                             ("test", "draft", "_delete", "tbd"))]
        out.append(parts)
    return out


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort coerce object columns that look numeric."""
    for col in df.columns:
        if df[col].dtype == object:
            coerced = pd.to_numeric(df[col], errors="coerce")
            if coerced.notna().sum() >= max(5, int(0.5 * df[col].notna().sum())):
                df[col] = coerced
    return df


def _coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().astype(str).head(50).tolist()
            if sum(bool(re.match(r"\d{4}-\d{2}-\d{2}", s)) for s in sample) >= 5:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    return df


def main():
    try:
        p = read_params()
        run_id = p.get("run_id") or "default"
        target = p.get("target") or "converted"
        multi = p.get("multi_value_fields") or [
            "events_attended", "webinars_attended", "email_nurtures_enrolled",
            "content_downloads", "pdf_downloads", "academy_registrations",
        ]
        exclude_values = {k: set(x.lower() for x in v)
                          for k, v in (p.get("exclude_values") or {}).items()}
        dedupe_on = p.get("dedupe_on") or ["id"]
        extra_excl = p.get("extra_excluded") or []

        if not p.get("input"):
            emit_error("input (path to parquet) is required")
            return

        df = _load_inputs(p["input"])
        warnings: list[str] = []

        # Dedupe
        pre = len(df)
        dedupe_cols = [c for c in dedupe_on if c in df.columns]
        if dedupe_cols:
            df = df.drop_duplicates(subset=dedupe_cols, keep="last")
        dropped = pre - len(df)
        if dropped:
            warnings.append(f"Deduplicated {dropped} rows on {dedupe_cols}")

        # Coerce numerics and dates
        df = _coerce_numeric(df)
        df = _coerce_dates(df)

        # Null-rate warnings
        for col in df.columns:
            null_rate = df[col].isna().mean()
            if null_rate > 0.5:
                warnings.append(
                    f"Column '{col}' is {null_rate:.0%} null — use with caution.")

        # Parse multi-value fields -> per-value binary columns + rollup + count
        for mcol in multi:
            if mcol not in df.columns:
                continue
            excl = exclude_values.get(mcol, set())
            parsed = _parse_multi(df[mcol].astype(str), excl)
            df[f"{mcol}_count"] = [len(x) for x in parsed]
            df[f"has_any_{mcol}"] = (df[f"{mcol}_count"] > 0).astype(int)
            values: set[str] = set()
            for lst in parsed: values.update(lst)
            for v in sorted(values):
                col_safe = re.sub(r"[^A-Za-z0-9]+", "_", v).strip("_").lower()
                if not col_safe: continue
                flag = f"{mcol}__{col_safe}"
                df[flag] = [int(v in lst) for lst in parsed]

        # Ordinal encodings
        sen_map = {**DEFAULT_SENIORITY_MAP, **(p.get("seniority_map") or {})}
        for col in ("seniority", "employment_role", "job_function", "jobtitle"):
            if col in df.columns:
                df[f"{col}_ord"] = _ordinal_encode(df[col], sen_map)
        if "revenue_range" in df.columns:
            df["revenue_range_ord"] = _ordinal_encode(df["revenue_range"],
                                                     REVENUE_RANGE_MAP)

        # Date-derived features
        for col in df.select_dtypes(include=["datetime64[ns, UTC]",
                                              "datetime64[ns]"]).columns:
            col_tz = df[col].dt.tz
            now = pd.Timestamp.now(tz=col_tz) if col_tz is not None \
                else pd.Timestamp.utcnow().tz_localize(None)
            days_col = f"days_since_{col}"
            df[days_col] = (now - df[col]).dt.days
            # Month index for trend work
            df[f"{col}_month_idx"] = df[col].dt.year * 12 + df[col].dt.month

        # Target construction
        rule = p.get("target_rule")
        if rule:
            if "expr" in rule:
                try:
                    df[target] = df.eval(rule["expr"]).astype(int)
                except Exception as e:
                    warnings.append(f"target_rule expr failed: {e}")
            elif "from_date" in rule:
                col = rule["from_date"]
                df[rule.get("name", target)] = df[col].notna().astype(int) \
                    if col in df.columns else 0
        elif target not in df.columns:
            # Default: if num_associated_deals exists, use has_deal
            if "num_associated_deals" in df.columns:
                df[target] = (pd.to_numeric(
                    df["num_associated_deals"], errors="coerce"
                    ).fillna(0) >= 1).astype(int)
                warnings.append(
                    f"Derived target '{target}' from num_associated_deals >= 1")
            else:
                warnings.append(
                    f"Target '{target}' not found and no rule provided; "
                    f"downstream skills will need an explicit target column.")

        # Final NaN imputation for numeric feature columns
        for col in df.select_dtypes(include=[np.number]).columns:
            if df[col].isna().any():
                df[col] = df[col].fillna(df[col].median())

        out_path = features_path(run_id)
        df.to_parquet(out_path, index=False)
        manifest = classify_columns(df, target_col=target,
                                    extra_excluded=extra_excl)
        manifest["run_id"] = run_id
        manifest["target"] = target
        manifest["features_path"] = str(out_path)
        manifest["n_records"] = int(len(df))
        manifest["baseline_rate"] = float(df[target].mean()) \
            if target in df.columns else None
        write_json(manifest_path(run_id), manifest)

        emit({
            "results": {
                "run_id": run_id,
                "target": target,
                "baseline_rate": manifest["baseline_rate"],
                "manifest_summary": {k: len(v) if isinstance(v, list) else v
                                     for k, v in manifest.items()
                                     if k not in ("features_path", "run_id",
                                                  "target", "baseline_rate")},
            },
            "summary": (
                f"Engineered {len(df.columns)} features across {len(df)} records "
                f"(run_id={run_id}, target={target}, baseline={manifest['baseline_rate']:.2%}"
                f" if manifest['baseline_rate'] is not None else 'N/A').".replace(
                    " if manifest['baseline_rate'] is not None else 'N/A'", "")),
            "metadata": {"n_records": len(df), "n_features": len(df.columns),
                         "warnings": warnings,
                         "artifacts": {"features": str(out_path),
                                       "manifest": str(manifest_path(run_id))}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
