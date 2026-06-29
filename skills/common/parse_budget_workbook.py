"""parse_budget_workbook — extract monthly marketing spend from the Apple Numbers
budget workbook into a tidy long CSV that load_spend can ingest.

The workbook ("Marketing Draft Budget Workbook") stores spend as detailed
line items across three monthly sheets:
  - "2025"          : Jan-Dec 2025, organized by section/subsection (col0/col1) + item (col2)
  - "Q126 Planning" : Jan-Mar 2026, organized by category HEADER rows + item
  - "Q226 Planning" : Apr-Jun 2026, same layout as Q1

Each line item is classified into ONE of seven faithful channels (keyword + section
context). load_spend then collapses these into the 4 modeling groups the MMM can
identify (see the marketing-mix-analyst canonical config). Faithful channels:
  events | linkedin_ads | google_ads | youtube_ads | outbound | email_website | organic_content

Rows the workbook owner flagged as wrong are NEVER emitted as spend (they have no
channel and fall through): "Total Pipeline Deals", "Cost Per Pipeline Deal (...)",
"Total Spend for modeling". Pull deal counts from HubSpot instead.

Params (JSON):
  input:    str   path to the .numbers workbook (required)
  output:   str   path to write the long CSV (default data/raw/spend_workbook_long.csv)
  end:      str   drop periods strictly after this YYYY-MM (default: keep all)
"""
from __future__ import annotations

import pandas as pd

from .io_contract import DATA_DIR, emit, emit_error, read_params

# Keyword rules, applied in priority order (outbound before ads so "LinkedIn Sales
# Nav" -> outbound, not paid_social; ads before content; content before events).
OUTBOUND = ["growth today", "growth-today", "growh-today", "clay", "orum", "lemlist",
            "instantly", "scaledmail", "scaled mail", "outbound sync", "outboundsync",
            "sales navigator", "sales nav", "tremendous", "gift card", "email account",
            "sending domain", "godaddy", "imti"]
CONTENT = ["video editor", "maksym", "maksim", "veed", "descript", "restream",
           "artlist", "shutterstock", "canva", "gamma", "voice over", "graphic design",
           "graphic designer", "hi its us", "hi it's us", "brand agency", "content"]
EVENTKW = ["event", "summit", "expo", "connect", "conference", "gtc", "dreamforce",
           "reuters", "momentum", "shoptalk", "finovate", "connexion", "unplugged",
           "accelerate", "gds ", "cmo", "synergy", "strategy insight",
           "strategic insight", "booth", "nvidia", "big data", "insight summit",
           "meet the boss", "world summit"]

# 2026 planning category headers -> faithful channel (digital ads split by name)
HEAD = {"MARTECH STACK": "email_website",
        "GRAPHIC DESIGN/CONTENT STACK": "organic_content",
        "VIDEO PRODUCTION STACK": "organic_content",
        "PRODUCT MARKETING SUPPORT": "organic_content",
        "OUTBOUND STACK": "outbound", "EVENTS": "events"}


def classify(name: str, header: str | None = None, sub: str | None = None):
    n = name.lower().strip()
    if any(k in n for k in OUTBOUND):
        return "outbound"
    if "google" in n:
        return "google_ads"
    if "youtube" in n:
        return "youtube_ads"
    if "linkedin" in n:        # sales-nav already caught above
        return "linkedin_ads"
    if "hubspot" in n or n.startswith("mention"):
        return "email_website"
    if any(k in n for k in CONTENT):
        return "organic_content"
    if (sub and ("event" in sub.lower() or "travel" in sub.lower())) \
            or header == "events" or any(k in n for k in EVENTKW):
        return "events"
    return None


def main():
    try:
        from numbers_parser import Document
    except ImportError:
        emit_error("numbers-parser not installed. `pip install numbers-parser`.")
        return
    try:
        p = read_params()
        if not p.get("input"):
            emit_error("input (path to .numbers workbook) is required")
            return
        out_path = p.get("output") or str(DATA_DIR / "raw" / "spend_workbook_long.csv")
        end = p.get("end")

        doc = Document(p["input"])
        sh = {s.name: s for s in doc.sheets}
        recs: list[tuple] = []

        # --- 2025 sheet: section(col0)/subsection(col1)/item(col2), Jan-Dec = cols 3..14
        months25 = [f"2025-{m:02d}" for m in range(1, 13)]
        if "2025" in sh:
            sub = ""
            for r in sh["2025"].tables[0].rows(values_only=True):
                c = [("" if v is None else v) for v in r]
                s1 = str(c[1]).strip() if len(c) > 1 and c[1] != "" else ""
                s2 = str(c[2]).strip() if len(c) > 2 and c[2] != "" else ""
                if len(c) > 0 and str(c[0]).strip():
                    sub = ""
                if s1:
                    sub = s1
                if not s2:
                    continue
                grp = classify(s2, sub=sub)
                if not grp:
                    continue
                for mi, v in enumerate(c[3:15]):
                    try:
                        x = float(v)
                    except (TypeError, ValueError):
                        continue
                    if x:
                        recs.append((months25[mi], grp, x, s2))

        # --- 2026 planning sheets: category HEADER rows, items with 3 monthly cols
        def parse2026(sheet: str, months: list[str]):
            if sheet not in sh:
                return
            header = "DIGITAL"
            for r in sh[sheet].tables[0].rows(values_only=True):
                c = [("" if v is None else v) for v in r]
                name = str(c[0]).strip() if c else ""
                if not name:
                    continue
                up = name.upper()
                if up.startswith("TREMENDOUS"):   # Tremendous gift card is part of outbound stack
                    header = "outbound"
                    continue
                if up in HEAD:
                    header = HEAD[up]
                    continue
                if up.startswith("TOTALS"):
                    break
                grp = classify(name) if header == "DIGITAL" else header
                if not grp:
                    continue
                for mi, v in enumerate(c[1:4]):
                    try:
                        x = float(v)
                    except (TypeError, ValueError):
                        continue
                    if x:
                        recs.append((months[mi], grp, x, name))

        parse2026("Q126 Planning", ["2026-01", "2026-02", "2026-03"])
        parse2026("Q226 Planning", ["2026-04", "2026-05", "2026-06"])

        df = pd.DataFrame(recs, columns=["period", "channel", "spend", "item"])
        if end:
            df = df[df["period"] <= end]
        df.to_csv(out_path, index=False)

        totals = df.groupby("channel")["spend"].sum().round(0).to_dict()
        emit({
            "results": {
                "channels": sorted(df["channel"].unique().tolist()),
                "total_spend_by_channel": {k: float(v) for k, v in totals.items()},
                "total_spend": float(df["spend"].sum()),
                "period_range": [df["period"].min(), df["period"].max()],
                "n_line_items": int(df["item"].nunique()),
            },
            "summary": (
                f"Extracted ${df['spend'].sum():,.0f} of spend across "
                f"{df['channel'].nunique()} faithful channels and "
                f"{df['period'].nunique()} months "
                f"({df['period'].min()}..{df['period'].max()}). Wrote {out_path}."),
            "metadata": {
                "n_records": int(len(df)), "n_features": int(df["channel"].nunique()),
                "warnings": [], "artifacts": {"csv": out_path}},
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
