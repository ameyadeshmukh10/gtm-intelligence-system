"""hs_pull_custom_properties — introspect available properties on any object type.

Workers MUST call this first whenever they are uncertain which field names
exist in the user's HubSpot instance. Program enrollment fields, stage-entry
date properties, and custom firmographic fields vary per install.

Params (JSON):
  object_type:   str   e.g. "deals" | "contacts" | "companies"
  filter_name:   str   optional substring to grep property names/labels
  show_options:  bool  include enumeration option lists (default False; verbose)
"""
from __future__ import annotations

import pandas as pd

from ..common.io_contract import emit, emit_error, hash_key, raw_path, read_params
from .client import HubSpotClient, HubSpotError


def main():
    try:
        p = read_params()
        object_type = p.get("object_type")
        if not object_type:
            emit_error("object_type is required")
            return
        filter_name = (p.get("filter_name") or "").lower()
        show_options = p.get("show_options", False)

        client = HubSpotClient()
        data = client.request("GET", f"/crm/v3/properties/{object_type}")
        props = data.get("results", [])

        rows = []
        for pr in props:
            name = pr.get("name", "")
            label = pr.get("label", "")
            if filter_name and filter_name not in name.lower() \
                    and filter_name not in label.lower():
                continue
            row = {
                "name": name, "label": label,
                "type": pr.get("type"), "fieldType": pr.get("fieldType"),
                "groupName": pr.get("groupName"),
                "description": (pr.get("description") or "")[:120],
                "calculated": pr.get("calculated", False),
                "hidden": pr.get("hidden", False),
                "hubspotDefined": pr.get("hubspotDefined", False),
            }
            if show_options:
                row["options"] = [o.get("value") for o in (pr.get("options") or [])]
            rows.append(row)

        df = pd.DataFrame(rows)
        key = hash_key({"obj": object_type, "filter": filter_name})
        out = raw_path(f"props_{object_type}", key)
        df.to_parquet(out, index=False)

        # Compact preview: names by group
        by_group: dict[str, list[str]] = {}
        for r in rows:
            by_group.setdefault(r["groupName"] or "ungrouped", []).append(r["name"])

        emit({
            "results": {
                "total_properties": len(rows),
                "by_group": {k: len(v) for k, v in sorted(by_group.items())},
                "sample_names": [r["name"] for r in rows[:30]],
            },
            "summary": (
                f"Found {len(rows)} properties on {object_type}"
                + (f" matching '{filter_name}'" if filter_name else "")
                + f". Full schema cached at {out}."),
            "metadata": {"n_records": len(rows),
                         "n_features": len(df.columns) if len(df) else 0,
                         "warnings": [], "artifacts": {"parquet": str(out)}},
        })
    except HubSpotError as e:
        emit_error(str(e))
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
