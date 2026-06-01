"""Helpers shared by py_* skills."""
from __future__ import annotations

import pandas as pd

from ..common.io_contract import load_manifest
from ..common.manifest import (feature_columns, numeric_columns,
                                binary_columns, categorical_columns)


def load_run(params: dict) -> tuple[pd.DataFrame, dict, str]:
    """Load features parquet + manifest for a run_id. Returns (df, manifest, target)."""
    run_id = params.get("run_id")
    if not run_id:
        raise ValueError("run_id is required")
    manifest = load_manifest(run_id)
    df = pd.read_parquet(manifest["features_path"])
    target = params.get("target") or manifest.get("target") or "converted"
    if target not in df.columns:
        raise ValueError(f"Target '{target}' not in feature matrix")
    return df, manifest, target


__all__ = ["load_run", "feature_columns", "numeric_columns",
           "binary_columns", "categorical_columns"]
