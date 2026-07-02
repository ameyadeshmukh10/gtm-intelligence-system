"""marketing_signals — the LOCKED definition of "marketing influence" for the
Marketing-Influence Cohort Report.

This is the single source of truth for what counts as an organic / direct / blog
marketing signal. It exists so the definitions live in exactly one place (not in
chat history, not re-typed per run). The influence_report skill and the
marketing-influence-analyst agent both depend on this module.

Locked signal set (confirmed with the user — NOT configurable):

  Contact-level (a contact carries a signal if ANY holds):
    - hs_analytics_source  in {ORGANIC_SEARCH, DIRECT_TRAFFIC}
    - hs_latest_source     in {ORGANIC_SEARCH, DIRECT_TRAFFIC}
    - blog activity: any of hs_analytics_first_url / hs_analytics_last_url /
      hs_analytics_first_referrer / hs_analytics_last_referrer contains "blog"

  Company-level (a company carries a signal if ANY holds):
    - hs_analytics_source in {ORGANIC_SEARCH, DIRECT_TRAFFIC}
    - LinkedIn organic: fibbler_linkedin_organic_impressions_<acct>_90_days > 0
                     OR fibbler_linkedin_organic_engagements_<acct>_90_days > 0
      (the <acct> account id is discovered dynamically, never hardcoded)

Rule compliance (docs/analysis_rules.md):
  - Rule 5 (source_offline is NOT marketing): ORGANIC_SEARCH / DIRECT_TRAFFIC are
    mutually exclusive with OFFLINE, so this cohort is clean by construction. Blog
    visits are genuine site engagement. We still surface source_offline counts for
    transparency but never let an OFFLINE-only contact into the influenced set.
"""
from __future__ import annotations

import re

import pandas as pd

# ---- Locked constants -------------------------------------------------------
ORGANIC = "ORGANIC_SEARCH"
DIRECT = "DIRECT_TRAFFIC"
ORGANIC_DIRECT = {ORGANIC, DIRECT}
BLOG_TOKEN = "blog"

CONTACT_SOURCE_COLS = ["hs_analytics_source", "hs_latest_source"]
CONTACT_URL_COLS = [
    "hs_analytics_first_url", "hs_analytics_last_url",
    "hs_analytics_first_referrer", "hs_analytics_last_referrer",
]
COMPANY_SOURCE_COLS = ["hs_analytics_source"]

# The exact contact + company properties the report needs pulled from HubSpot.
CONTACT_PROPERTIES = [
    "email", "firstname", "lastname", "jobtitle",
    "hs_analytics_source", "hs_latest_source",
    "hs_analytics_first_url", "hs_analytics_last_url",
    "hs_analytics_first_referrer", "hs_analytics_last_referrer",
    "lifecyclestage", "createdate",
]
COMPANY_PROPERTIES = [
    "name", "domain", "hs_analytics_source", "lifecyclestage", "createdate",
]

_LI_ORGANIC_RE = re.compile(
    r"^fibbler_linkedin_organic_(impressions|engagements)_\d+_90_days$", re.I)


def discover_linkedin_organic_props(client) -> list[str]:
    """Introspect company properties and return the instance-specific
    fibbler LinkedIn *organic* 90-day impression/engagement property names.

    Returns [] if the Fibbler integration isn't present — the report then simply
    skips the company-LinkedIn-organic signal (contact + company-source signals
    still apply)."""
    try:
        data = client.request("GET", "/crm/v3/properties/companies")
    except Exception:
        return []
    return sorted(p.get("name", "") for p in data.get("results", [])
                  if _LI_ORGANIC_RE.match(p.get("name", "") or ""))


def _blog_hit(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.contains(BLOG_TOKEN, case=False, regex=False)


def _source_hit(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.upper().isin(ORGANIC_DIRECT)


def contact_signal_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add per-contact boolean signal columns + `influenced`. Returns a copy with
    columns: sig_organic, sig_direct, sig_blog, sig_offline, influenced."""
    out = df.copy()
    src_upper = pd.DataFrame({c: out[c].fillna("").astype(str).str.upper()
                              for c in CONTACT_SOURCE_COLS if c in out.columns})
    out["sig_organic"] = (src_upper == ORGANIC).any(axis=1) if len(src_upper.columns) else False
    out["sig_direct"] = (src_upper == DIRECT).any(axis=1) if len(src_upper.columns) else False

    blog = pd.Series(False, index=out.index)
    for c in CONTACT_URL_COLS:
        if c in out.columns:
            blog = blog | _blog_hit(out[c])
    out["sig_blog"] = blog

    out["sig_offline"] = (src_upper == "OFFLINE").any(axis=1) if len(src_upper.columns) else False
    out["influenced"] = out["sig_organic"] | out["sig_direct"] | out["sig_blog"]
    return out


def company_signal_flags(df: pd.DataFrame, li_props: list[str]) -> pd.DataFrame:
    """Add per-company boolean signal columns + `influenced`. Returns a copy with
    columns: sig_organic, sig_direct, sig_li_organic, influenced."""
    out = df.copy()
    if "hs_analytics_source" in out.columns:
        src = out["hs_analytics_source"].fillna("").astype(str).str.upper()
        out["sig_organic"] = src == ORGANIC
        out["sig_direct"] = src == DIRECT
    else:
        out["sig_organic"] = False
        out["sig_direct"] = False

    li = pd.Series(False, index=out.index)
    for c in li_props:
        if c in out.columns:
            li = li | (pd.to_numeric(out[c], errors="coerce").fillna(0) > 0)
    out["sig_li_organic"] = li

    out["influenced"] = out["sig_organic"] | out["sig_direct"] | out["sig_li_organic"]
    return out


def target_definition_text(cap_company_contacts: int) -> str:
    """The plain-English target definition (Rule 6) embedded in every report."""
    return (
        "A deal is 'influenced' if ANY of its associated contacts, its associated "
        "companies, or up to "
        f"{cap_company_contacts} of each company's other associated contacts shows "
        "an organic/direct/blog marketing signal. Signals: contact "
        "hs_analytics_source or hs_latest_source in {ORGANIC_SEARCH, DIRECT_TRAFFIC}; "
        "contact first/last URL or referrer containing 'blog'; company "
        "hs_analytics_source in {ORGANIC_SEARCH, DIRECT_TRAFFIC}; company LinkedIn "
        "organic impressions or engagements (90d) > 0. OFFLINE-sourced contacts are "
        "excluded by construction (Rule 5). 'Cold' = a deal with none of these signals."
    )
