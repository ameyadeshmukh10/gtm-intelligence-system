"""Helpers used by every hs_pull_* skill."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..common.io_contract import emit, emit_error, hash_key, raw_path
from .client import HubSpotClient, HubSpotError, build_filter_groups, flatten


def run_object_pull(*, object_type: str, params: dict,
                    default_properties: list[str]) -> None:
    """Common pull-loop for CRM objects (deals, contacts, companies).

    params:
      properties:   list[str]       # columns to pull; default_properties used otherwise
      filters:      list[dict]      # {property, operator, value}
      associations: list[str]       # related object types
      limit:        int | "all"
      use_search:   bool            # force POST /search (needed for filters)
    """
    try:
        properties = params.get("properties") or default_properties
        filters = params.get("filters") or []
        associations = params.get("associations") or []
        limit = params.get("limit", "all")
        use_search = bool(filters) or bool(params.get("use_search"))

        client = HubSpotClient()
        if use_search:
            filter_groups = build_filter_groups(filters)
            records = list(client.paginate_search(
                object_type, properties=properties,
                filter_groups=filter_groups, associations=associations,
                limit=limit))
        else:
            records = list(client.paginate_list(
                f"/crm/v3/objects/{object_type}", properties=properties,
                associations=associations, limit=limit))

        rows = [flatten(r) for r in records]
        df = pd.DataFrame(rows)
        key = hash_key({"obj": object_type, "params": params})
        out = raw_path(object_type, key)
        df.to_parquet(out, index=False)

        warnings = []
        if not records:
            warnings.append(
                f"No {object_type} records returned — check filter scope or token access.")
        emit({
            "results": {
                "object_type": object_type,
                "columns": list(df.columns),
                "sample": df.head(3).to_dict(orient="records") if len(df) else [],
            },
            "summary": (
                f"Pulled {len(df)} {object_type} records with "
                f"{len(df.columns)} properties. Cached at {out}."),
            "metadata": {
                "n_records": len(df), "n_features": len(df.columns),
                "warnings": warnings,
                "artifacts": {"parquet": str(out)},
            },
        })
    except HubSpotError as e:
        emit_error(str(e))
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")
