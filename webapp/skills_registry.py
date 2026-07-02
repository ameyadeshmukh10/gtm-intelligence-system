"""Catalog of runnable skills + canonical multi-step pipelines.

This is the single source of truth the UI uses to know what can be run. Skills
are invoked unchanged (webapp.jobs.run_skill). Pipelines chain several skills,
threading each step's output artifact into the next via a shared context dict —
this is what reproduces the marketing-mix-analyst's canonical command chain
(see .claude/agents/marketing-mix-analyst.md).
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import RAW_DIR, BUDGET_WORKBOOK_PATH, CANONICAL_MMM_RUN_ID

# -----------------------------------------------------------------------------
# Individual skills (for the audit browser's "run a skill" affordance + docs)
# -----------------------------------------------------------------------------
SKILLS: dict[str, dict[str, Any]] = {
    "hs_pull_deals":      {"module": "skills.hubspot.hs_pull_deals",      "group": "hubspot", "long_running": True,  "label": "Pull deals"},
    "hs_pull_contacts":   {"module": "skills.hubspot.hs_pull_contacts",   "group": "hubspot", "long_running": True,  "label": "Pull contacts"},
    "hs_pull_engagements":{"module": "skills.hubspot.hs_pull_engagements","group": "hubspot", "long_running": True,  "label": "Pull engagements"},
    "parse_budget_workbook": {"module": "skills.common.parse_budget_workbook", "group": "spend", "long_running": False, "label": "Parse budget workbook"},
    "load_spend":         {"module": "skills.common.load_spend",          "group": "spend",   "long_running": False, "label": "Normalize spend"},
    "py_feature_engineering": {"module": "skills.python.py_feature_engineering", "group": "prep", "long_running": False, "label": "Feature engineering"},
    "py_mmm_features":    {"module": "skills.python.py_mmm_features",      "group": "mmm",     "long_running": False, "label": "Build MMM design matrix"},
    "py_marketing_mix_model": {"module": "skills.python.py_marketing_mix_model", "group": "mmm", "long_running": False, "label": "Fit MMM"},
    "py_trend_analysis":  {"module": "skills.python.py_trend_analysis",   "group": "audit",   "long_running": False, "label": "Trend analysis"},
    "py_categorical_conversion": {"module": "skills.python.py_categorical_conversion", "group": "audit", "long_running": False, "label": "Categorical conversion"},
    "py_random_forest":   {"module": "skills.python.py_random_forest",    "group": "audit",   "long_running": False, "label": "Random forest"},
    "py_kmeans_cluster":  {"module": "skills.python.py_kmeans_cluster",   "group": "audit",   "long_running": False, "label": "KMeans clusters"},
    "py_cohort_analysis": {"module": "skills.python.py_cohort_analysis",  "group": "audit",   "long_running": False, "label": "Cohort analysis"},
    "py_stage_conversion":{"module": "skills.python.py_stage_conversion", "group": "audit",   "long_running": False, "label": "Stage conversion"},
    "influence_report":   {"module": "skills.reports.influence_report",    "group": "report",  "long_running": True,  "label": "Marketing influence report"},
}

# Canonical 4-group config (mirrors the marketing-mix-analyst canonical run).
MMM_CHANNEL_MAP = {"events": "events", "linkedin_ads": "paid_social",
                   "youtube_ads": "paid_social", "google_ads": "paid_search",
                   "outbound": "outbound"}
MMM_DROP_CHANNELS = ["organic_content", "email_website"]
MMM_DEAL_FILTER = ("pipeline == 'default' and createdate >= '2025-01-01' "
                   "and createdate < '2026-06-01'")


def newest(prefix: str) -> str | None:
    files = glob.glob(str(RAW_DIR / f"{prefix}_*.parquet"))
    return max(files, key=os.path.getmtime) if files else None


def _artifact(envelope: dict, kind: str = "parquet") -> str | None:
    return ((envelope or {}).get("metadata", {}).get("artifacts", {}) or {}).get(kind)


@dataclass
class Step:
    name: str
    module: str
    build: Callable[[dict], dict]                       # ctx -> params
    capture: Callable[[dict, dict], None] = field(      # (ctx, envelope) -> None
        default=lambda ctx, env: None)


@dataclass
class Pipeline:
    key: str
    label: str
    description: str
    steps: list[Step]


def _csv_out(ctx):  # parse_budget_workbook output path
    return str(RAW_DIR / "spend_workbook_long.csv")


PIPELINES: dict[str, Pipeline] = {
    "refresh_deals": Pipeline(
        key="refresh_deals", label="Refresh deals (HubSpot)",
        description="Pull all deals from HubSpot into a fresh parquet (the MMM outcome source).",
        steps=[
            Step("pull_deals", SKILLS["hs_pull_deals"]["module"],
                 build=lambda ctx: {"limit": "all"},
                 capture=lambda ctx, env: ctx.update(deals_input=_artifact(env))),
        ]),
    "rebuild_mmm": Pipeline(
        key="rebuild_mmm", label="Rebuild Marketing Mix Model",
        description="Canonical 4-group MMM: parse workbook -> normalize spend -> "
                    "build design matrix -> fit. Uses the newest deals parquet "
                    "(run 'Refresh deals' first for fresh data).",
        steps=[
            Step("parse_workbook", SKILLS["parse_budget_workbook"]["module"],
                 build=lambda ctx: {"input": ctx.get("workbook") or BUDGET_WORKBOOK_PATH,
                                    "output": _csv_out(ctx), "end": "2026-05"},
                 capture=lambda ctx, env: ctx.update(spend_csv=_artifact(env, "csv") or _csv_out(ctx))),
            Step("normalize_spend", SKILLS["load_spend"]["module"],
                 build=lambda ctx: {"input": ctx["spend_csv"], "granularity": "month",
                                    "layout": "long", "channel_map": MMM_CHANNEL_MAP,
                                    "drop_channels": MMM_DROP_CHANNELS},
                 capture=lambda ctx, env: ctx.update(spend_input=_artifact(env))),
            Step("mmm_features", SKILLS["py_mmm_features"]["module"],
                 build=lambda ctx: {"run_id": CANONICAL_MMM_RUN_ID,
                                    "deals_input": ctx.get("deals_input") or newest("deals"),
                                    "spend_input": ctx["spend_input"], "granularity": "month",
                                    "outcome": "deals_created", "deal_filter": MMM_DEAL_FILTER}),
            Step("fit_mmm", SKILLS["py_marketing_mix_model"]["module"],
                 build=lambda ctx: {"run_id": CANONICAL_MMM_RUN_ID, "backend": "laplace", "seed": 7}),
        ]),
}
