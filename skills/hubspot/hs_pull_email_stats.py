"""hs_pull_email_stats — per-contact email engagement (sent/delivered/opened/clicked).

Two modes:
  (a) "contact_rollup" (default) — pull per-contact engagement properties
      via the contacts endpoint. Fast, one property set.
  (b) "event_stream" — pull email events via the legacy Email Events API
      /email/public/v1/events. Slower but gives per-event granularity.

Params (JSON):
  mode:    "contact_rollup" | "event_stream"  default "contact_rollup"
  since:   ISO date                           only for event_stream
  until:   ISO date
  limit:   int | "all"
"""
from __future__ import annotations

import datetime as dt
import pandas as pd

from ..common.io_contract import emit, emit_error, hash_key, raw_path, read_params
from .client import HubSpotClient, HubSpotError, flatten

EMAIL_PROPERTIES = [
    "email",
    "hs_email_delivered", "hs_email_open", "hs_email_click",
    "hs_email_bounce", "hs_email_hard_bounced", "hs_email_soft_bounced",
    "hs_email_optout", "hs_email_sends_since_last_engagement",
    "hs_email_first_open_date", "hs_email_last_open_date",
    "hs_email_first_click_date", "hs_email_last_click_date",
]


def _iso_to_ms(s: str | None) -> int | None:
    if not s:
        return None
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def main():
    try:
        p = read_params()
        mode = p.get("mode", "contact_rollup")
        limit = p.get("limit", "all")
        client = HubSpotClient()

        if mode == "contact_rollup":
            records = list(client.paginate_list(
                "/crm/v3/objects/contacts",
                properties=EMAIL_PROPERTIES, limit=limit))
            df = pd.DataFrame([flatten(r) for r in records])
            key = hash_key({"mode": mode})
            out = raw_path("email_rollup", key)
            df.to_parquet(out, index=False)
            emit({
                "results": {"columns": list(df.columns)},
                "summary": (f"Pulled email rollup for {len(df)} contacts. "
                            f"Cached at {out}."),
                "metadata": {"n_records": len(df),
                             "n_features": len(df.columns),
                             "warnings": [],
                             "artifacts": {"parquet": str(out)}},
            })
            return

        # event_stream mode
        since = _iso_to_ms(p.get("since"))
        until = _iso_to_ms(p.get("until"))
        params: dict = {"limit": 1000}
        if since: params["startTimestamp"] = since
        if until: params["endTimestamp"] = until

        rows: list[dict] = []
        offset = None
        while True:
            if offset: params["offset"] = offset
            data = client.request("GET", "/email/public/v1/events", params=params)
            for ev in data.get("events", []):
                rows.append({
                    "event_id": ev.get("id"),
                    "recipient": ev.get("recipient"),
                    "type": ev.get("type"),
                    "created": ev.get("created"),
                    "email_campaign_id": ev.get("emailCampaignId"),
                })
                if limit != "all" and len(rows) >= int(limit):
                    break
            if limit != "all" and len(rows) >= int(limit):
                break
            if not data.get("hasMore"):
                break
            offset = data.get("offset")
            if not offset:
                break
        df = pd.DataFrame(rows)
        key = hash_key({"mode": mode, "since": since, "until": until})
        out = raw_path("email_events", key)
        df.to_parquet(out, index=False)
        by_type = df["type"].value_counts().to_dict() if len(df) else {}
        emit({
            "results": {"by_type": by_type, "columns": list(df.columns)},
            "summary": f"Pulled {len(df)} email events. Cached at {out}.",
            "metadata": {"n_records": len(df), "n_features": len(df.columns),
                         "warnings": [], "artifacts": {"parquet": str(out)}},
        })
    except HubSpotError as e:
        emit_error(str(e))
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
