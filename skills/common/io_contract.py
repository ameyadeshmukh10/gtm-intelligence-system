"""Shared I/O contract for every skill.

Every skill:
  1. accepts params as a JSON string (argv[1]) OR via stdin
  2. writes full result to a file in data/ (parquet for tabular, json for analytical)
  3. prints a compact JSON summary to stdout so agents see small context

Output envelope:
  {
    "results":  <dict or list — structured analytical result>,
    "summary":  <2-3 sentence plain-English summary>,
    "metadata": {"n_records": int, "n_features": int, "warnings": [str, ...],
                 "artifacts": {<kind>: <path>, ...}}
  }
"""
from __future__ import annotations

import json
import os
import sys
import hashlib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ.get("PIPELINE_DATA_DIR", "./data")).resolve()
(DATA_DIR / "raw").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "features").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "results").mkdir(parents=True, exist_ok=True)


def read_params() -> dict:
    """Read JSON params from argv[1] or stdin."""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        raw = sys.argv[1]
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        emit_error(f"Invalid JSON params: {e}")
        sys.exit(2)


def emit(envelope: dict[str, Any]) -> None:
    """Print envelope as compact JSON to stdout."""
    envelope.setdefault("metadata", {}).setdefault("warnings", [])
    print(json.dumps(envelope, default=str))


def emit_error(message: str, warnings: list[str] | None = None) -> None:
    emit({
        "results": None,
        "summary": f"ERROR: {message}",
        "metadata": {"n_records": 0, "n_features": 0,
                     "warnings": warnings or [], "error": message},
    })


def hash_key(obj: Any, length: int = 10) -> str:
    payload = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:length]


def raw_path(name: str, key: str) -> Path:
    return DATA_DIR / "raw" / f"{name}_{key}.parquet"


def features_path(run_id: str) -> Path:
    return DATA_DIR / "features" / f"{run_id}.parquet"


def manifest_path(run_id: str) -> Path:
    return DATA_DIR / "features" / f"{run_id}_manifest.json"


def results_path(run_id: str, skill: str) -> Path:
    return DATA_DIR / "results" / f"{run_id}_{skill}.json"


def load_manifest(run_id: str) -> dict:
    p = manifest_path(run_id)
    if not p.exists():
        raise FileNotFoundError(
            f"Manifest not found for run_id={run_id}. "
            f"Run py_feature_engineering first.")
    return json.loads(p.read_text())


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, default=str, indent=2))
