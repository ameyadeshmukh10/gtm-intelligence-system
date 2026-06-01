"""Column manifest — every downstream skill reads this to auto-resolve features.

Categories:
  id:          identifier columns (contact_id, deal_id, ...) — never used as features
  target:      binary outcome columns (converted, has_deal, ...)
  numeric:     continuous numeric features
  ordinal:     ordinal-encoded categoricals (seniority_ord, revenue_range_ord)
  categorical: string categoricals used by py_categorical_conversion
  binary:      0/1 flags (program enrollment, conversion touchpoints)
  rollup:      has_any_* binary rollups of multi-value fields
  date:        datetime columns
  date_derived: days_since_*, month_idx, weekday (numeric)
  excluded:    columns known to leak the target (meeting_booked, etc.)
"""
from __future__ import annotations

from typing import Iterable

# Columns that are downstream consequences of deal existence — always exclude.
CONTAMINATION_COLUMNS: set[str] = {
    "meeting_booked", "hs_meeting_booked", "demo_completed",
    "sql_created", "mql_created", "opportunity_created",
    "closed_date", "closedate", "hs_closedate",
}


def classify_columns(df, target_col: str | None = None,
                     extra_excluded: Iterable[str] = ()) -> dict:
    """Build manifest from a pandas DataFrame after feature engineering."""
    import pandas as pd

    excluded = CONTAMINATION_COLUMNS | set(extra_excluded or ())

    m = {"id": [], "target": [], "numeric": [], "ordinal": [],
         "categorical": [], "binary": [], "rollup": [], "date": [],
         "date_derived": [], "excluded": sorted(excluded)}

    for col in df.columns:
        lname = col.lower()
        if lname in excluded:
            continue
        if col == target_col:
            m["target"].append(col)
            continue
        if lname.endswith("_id") or lname in {"id", "contact_id", "deal_id", "company_id"}:
            m["id"].append(col)
            continue
        if lname.startswith("has_any_"):
            m["rollup"].append(col)
            continue
        dtype = df[col].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype):
            m["date"].append(col)
            continue
        if lname.endswith("_ord"):
            m["ordinal"].append(col)
            continue
        if pd.api.types.is_bool_dtype(dtype):
            m["binary"].append(col)
            continue
        if pd.api.types.is_numeric_dtype(dtype):
            vals = df[col].dropna().unique()
            if len(vals) <= 2 and set(vals).issubset({0, 1, 0.0, 1.0}):
                m["binary"].append(col)
            elif lname.startswith(("days_", "month_", "weekday_")) or lname.endswith(
                    ("_count", "_sessions", "_pageviews", "_clicks", "_opens")):
                m["date_derived" if lname.startswith(("days_", "month_", "weekday_"))
                   else "numeric"].append(col)
            else:
                m["numeric"].append(col)
        else:
            # string or categorical
            nunique = df[col].nunique(dropna=True)
            if nunique <= 50:
                m["categorical"].append(col)
            # else: free-text — leave unclassified

    return m


def feature_columns(manifest: dict, include_rollups: bool = True,
                    include_binary: bool = True) -> list[str]:
    """Resolve the full feature matrix column list from the manifest."""
    cols = manifest.get("numeric", []) + manifest.get("ordinal", []) + \
           manifest.get("date_derived", [])
    if include_binary:
        cols = cols + manifest.get("binary", [])
    if include_rollups:
        cols = cols + manifest.get("rollup", [])
    # dedupe preserving order
    seen, out = set(), []
    for c in cols:
        if c not in seen:
            out.append(c); seen.add(c)
    return out


def numeric_columns(manifest: dict) -> list[str]:
    return manifest.get("numeric", []) + manifest.get("ordinal", []) + \
           manifest.get("date_derived", [])


def binary_columns(manifest: dict) -> list[str]:
    return manifest.get("binary", []) + manifest.get("rollup", [])


def categorical_columns(manifest: dict) -> list[str]:
    return manifest.get("categorical", [])
