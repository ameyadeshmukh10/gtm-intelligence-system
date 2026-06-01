"""hs_pull_marketing_events — retrieve events + per-contact attendance.

Params (JSON):
  include_attendance: bool          default True — pull per-contact attendance for each event
  states:            list[str]      subset of {"registered","attended","cancelled","noshow"}; default all
  limit:             int | "all"
"""
from __future__ import annotations

import pandas as pd

from ..common.io_contract import emit, emit_error, hash_key, raw_path, read_params
from .client import HubSpotClient, HubSpotError

ATTENDANCE_STATES = ["registered", "attended", "cancelled", "noshow"]


def main():
    try:
        p = read_params()
        include_attendance = p.get("include_attendance", True)
        states = p.get("states") or ATTENDANCE_STATES
        limit = p.get("limit", "all")

        client = HubSpotClient()
        events = list(client.paginate_list(
            "/marketing/v3/marketing-events", page_size=100, limit=limit))
        events_df = pd.DataFrame(events)

        attendance_rows: list[dict] = []
        if include_attendance and events:
            for ev in events:
                ev_id = ev.get("objectId") or ev.get("id") or ev.get("externalEventId")
                if not ev_id:
                    continue
                for state in states:
                    try:
                        data = client.request(
                            "GET",
                            f"/marketing/v3/marketing-events/"
                            f"{ev_id}/attendance/{state}/read")
                        for rec in (data.get("results") or []):
                            attendance_rows.append({
                                "event_id": ev_id,
                                "event_name": ev.get("eventName"),
                                "event_start": ev.get("eventStartTime"),
                                "state": state,
                                "contact_id": rec.get("contactId")
                                              or rec.get("vid"),
                                "interaction_date": rec.get("interactionDateTime"),
                            })
                    except HubSpotError:
                        # some events may not have attendance tracked
                        continue

        att_df = pd.DataFrame(attendance_rows)
        key = hash_key({"states": sorted(states), "attn": include_attendance})
        ev_out = raw_path("marketing_events", key)
        events_df.to_parquet(ev_out, index=False)
        artifacts = {"events_parquet": str(ev_out)}
        if len(att_df):
            att_out = raw_path("event_attendance", key)
            att_df.to_parquet(att_out, index=False)
            artifacts["attendance_parquet"] = str(att_out)

        emit({
            "results": {
                "events_count": len(events_df),
                "attendance_records": len(att_df),
                "states_breakdown": (att_df["state"].value_counts().to_dict()
                                     if len(att_df) else {}),
            },
            "summary": (
                f"Pulled {len(events_df)} marketing events"
                + (f" with {len(att_df)} attendance records"
                   if include_attendance else "")
                + f". Cached at {ev_out}."),
            "metadata": {
                "n_records": len(events_df) + len(att_df),
                "n_features": len(events_df.columns),
                "warnings": [], "artifacts": artifacts,
            },
        })
    except HubSpotError as e:
        emit_error(str(e))
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
