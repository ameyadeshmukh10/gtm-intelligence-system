"""hs_pull_associations — resolve object-to-object associations.

Params (JSON):
  from_type:  "deals" | "contacts" | "companies" | ...
  to_type:    "contacts" | "companies" | "deals" | ...
  ids:        list[str]   # source object IDs
  ids_from:   str         # OR: load source IDs from a parquet file ("id" column)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..common.io_contract import (read_params, emit, emit_error, hash_key, raw_path)
from .client import HubSpotClient, HubSpotError


def main():
    try:
        p = read_params()
        from_type = p["from_type"]
        to_type = p["to_type"]
        ids = p.get("ids") or []
        if not ids and p.get("ids_from"):
            df_src = pd.read_parquet(p["ids_from"])
            ids = df_src["id"].dropna().astype(str).unique().tolist()
        if not ids:
            emit_error("No source IDs provided — pass ids or ids_from")
            return
        client = HubSpotClient()
        mapping = client.associations_batch_read(from_type, to_type, ids)
        # Emit as a tall dataframe: from_id, to_id
        rows = [{"from_id": k, "to_id": v} for k, vs in mapping.items() for v in vs]
        df = pd.DataFrame(rows)
        key = hash_key({"from": from_type, "to": to_type, "ids": sorted(ids)[:50]})
        out = raw_path(f"assoc_{from_type}_{to_type}", key)
        df.to_parquet(out, index=False)
        emit({
            "results": {
                "pairs_count": len(df),
                "unique_from": df["from_id"].nunique() if len(df) else 0,
                "unique_to": df["to_id"].nunique() if len(df) else 0,
            },
            "summary": (
                f"Resolved {len(df)} {from_type}->{to_type} association pairs "
                f"across {df['from_id'].nunique() if len(df) else 0} source objects. "
                f"Cached at {out}."),
            "metadata": {
                "n_records": len(df), "n_features": 2, "warnings": [],
                "artifacts": {"parquet": str(out)},
            },
        })
    except HubSpotError as e:
        emit_error(str(e))
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
