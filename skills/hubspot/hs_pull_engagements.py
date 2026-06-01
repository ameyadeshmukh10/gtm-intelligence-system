"""hs_pull_engagements — calls, emails, meetings, notes, tasks.

Pulls across the engagement object types and, if association IDs are provided,
filters to engagements linked to those objects (e.g. deal IDs). Otherwise
pulls all engagements within a date range.

Params (JSON):
  engagement_types: list[str]     default ["calls","emails","meetings","notes","tasks"]
  associated_type:  str           optional — "deals" | "contacts" | "companies"
  associated_ids:   list[str]     optional — filter to engagements linked to these ids
  since:            str           ISO date — only pull engagements >= this timestamp
  until:            str           ISO date — only pull engagements < this timestamp
  limit:            int | "all"
"""
from __future__ import annotations

import pandas as pd

from ..common.io_contract import emit, emit_error, hash_key, raw_path, read_params
from .client import HubSpotClient, HubSpotError, build_filter_groups, flatten

ENG_TYPES = ["calls", "emails", "meetings", "notes", "tasks"]

# Per-type default property sets
PROPS = {
    "calls": ["hs_call_title", "hs_call_duration", "hs_call_direction",
              "hs_call_disposition", "hs_call_from_number", "hs_call_to_number",
              "hs_timestamp", "hubspot_owner_id"],
    "emails": ["hs_email_subject", "hs_email_direction", "hs_email_status",
               "hs_timestamp", "hubspot_owner_id"],
    "meetings": ["hs_meeting_title", "hs_meeting_outcome", "hs_meeting_start_time",
                 "hs_meeting_end_time", "hs_timestamp", "hubspot_owner_id"],
    "notes": ["hs_note_body", "hs_timestamp", "hubspot_owner_id"],
    "tasks": ["hs_task_subject", "hs_task_status", "hs_task_priority",
              "hs_task_type", "hs_timestamp", "hubspot_owner_id"],
}


def main():
    try:
        p = read_params()
        types = p.get("engagement_types") or ENG_TYPES
        since = p.get("since")
        until = p.get("until")
        assoc_type = p.get("associated_type")
        assoc_ids = set(p.get("associated_ids") or [])
        limit = p.get("limit", "all")

        client = HubSpotClient()
        all_rows: list[dict] = []

        for et in types:
            properties = PROPS.get(et, ["hs_timestamp"])
            filters = []
            if since:
                filters.append({"property": "hs_timestamp", "operator": "GTE",
                                "value": since})
            if until:
                filters.append({"property": "hs_timestamp", "operator": "LT",
                                "value": until})
            associations = [assoc_type] if assoc_type else []
            if filters:
                records = list(client.paginate_search(
                    et, properties=properties,
                    filter_groups=build_filter_groups(filters),
                    associations=associations, limit=limit))
            else:
                records = list(client.paginate_list(
                    f"/crm/v3/objects/{et}", properties=properties,
                    associations=associations, limit=limit))

            for r in records:
                row = flatten(r, extra={"engagement_type": et})
                if assoc_ids:
                    linked = set((row.get(f"assoc_{assoc_type}_ids") or "").split(";"))
                    if not (linked & assoc_ids):
                        continue
                all_rows.append(row)

        df = pd.DataFrame(all_rows)
        key = hash_key({"types": sorted(types), "since": since, "until": until,
                        "assoc_type": assoc_type, "ids": sorted(assoc_ids)[:50]})
        out = raw_path("engagements", key)
        df.to_parquet(out, index=False)

        by_type = df["engagement_type"].value_counts().to_dict() if len(df) else {}
        emit({
            "results": {"by_type": by_type, "columns": list(df.columns)},
            "summary": (
                f"Pulled {len(df)} engagements across {len(types)} types"
                + (f" filtered to {assoc_type}={len(assoc_ids)} ids" if assoc_ids else "")
                + f". Cached at {out}."),
            "metadata": {"n_records": len(df), "n_features": len(df.columns),
                         "warnings": [], "artifacts": {"parquet": str(out)}},
        })
    except HubSpotError as e:
        emit_error(str(e))
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
