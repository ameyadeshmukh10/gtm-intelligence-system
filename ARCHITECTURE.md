# Architecture — GTM Intelligence System

This document explains in detail how this repository works: every layer, every contract, the data flow, the routing logic, the rules engine, and how a single user prompt becomes a deliverable. If you read this file end-to-end you will understand the system well enough to extend it, debug it, and reason about why it produces the outputs it does.

---

## Table of contents

1. [What this system does](#1-what-this-system-does)
2. [The mental model](#2-the-mental-model-llm-reasons-python-computes)
3. [Three-layer architecture](#3-three-layer-architecture)
4. [Layer 1 — HubSpot connector skills](#4-layer-1--hubspot-connector-skills)
5. [Layer 2 — Python statistical skills](#5-layer-2--python-statistical-skills)
6. [Layer 3 — Agent brains](#6-layer-3--agent-brains)
7. [The skill contract](#7-the-skill-contract)
8. [The manifest-driven design](#8-the-manifest-driven-design)
9. [The orchestrator](#9-the-orchestrator)
10. [Caching strategy](#10-caching-strategy)
11. [Analytical rules engine](#11-analytical-rules-engine)
12. [Memory persistence](#12-memory-persistence)
13. [End-to-end walkthrough: "run a full GTM audit"](#13-end-to-end-walkthrough-run-a-full-gtm-audit)
14. [Extension patterns](#14-extension-patterns)
15. [Known limits and technical debt](#15-known-limits-and-technical-debt)
16. [Why these choices](#16-why-these-choices)
17. [Marketing Mix Model (top-down extension)](#17-marketing-mix-model-top-down-extension)
18. [Web application](#18-web-application)

---

## 1. What this system does

This is an **agentic GTM analytics system** built on top of HubSpot. A user, working in a Claude Code chat, asks a GTM question in plain English — "what predicts whether a Stage 0 deal advances to Stage 1?", "which marketing programs produce pipeline?", "run a full GTM audit" — and the system:

1. Decomposes the question into analytical tasks.
2. Pulls live data from HubSpot via the CRM API (deals, contacts, companies, associations, engagements, marketing events).
3. Runs deterministic statistical analyses in Python (Mann-Whitney U, chi², Random Forest, KMeans, Spearman correlation, stage conversion, interaction effects).
4. Interprets the results through agent reasoning, applying domain rules (n thresholds, leakage detection, correlation-not-causation discipline).
5. Delivers a written briefing in the format a VP of Sales or CMO can act on — not a raw stats dump.

It is a port of the **EverWorker GTM Intelligence System v2** spec (see `EverWorker_GTM_Intelligence_System_v2-1.docx` in the repo root) from EverWorker's hosted-agent platform to Claude Code's native subagent infrastructure. Same conceptual architecture; different runtime.

The output is **not** a dashboard, not a slide deck, not a CSV export. It is interpretive analysis in markdown, written for human decision-makers, with every claim traceable to specific HubSpot data.

> **Two extensions have since been added** (documented in §17–§18): a **Marketing Mix Model** — a top-down, spend→pipeline Bayesian model that *complements* the bottom-up attribution analysts — and an optional **web application** (`webapp/`) that provides a UI and SQLite storage for running analyses and visualizing results without a chat window. The core principle above still holds for the analytics layer; the web app is a separate consumption surface over the same on-disk artifacts.

---

## 2. The mental model: LLM reasons, Python computes

This is the single most important principle in the design.

| Layer | What it does | What it never does |
|---|---|---|
| **Python skills** | Deterministic math: Mann-Whitney U, chi-square, Random Forest fits, Spearman correlations, k-means, feature engineering, parquet I/O, HubSpot HTTP calls with pagination | Make judgments. Decide which test to run. Interpret a p-value. Recommend an action. Decide which features matter. |
| **LLM agents** | Pick which skills to invoke and in what order. Read structured JSON results. Apply interpretation rules (small-n discipline, leakage detection, correlation-not-causation). Synthesize across multiple analyses. Write the briefing. | Compute statistics. Loop over records. Manually paginate. Hold large dataframes in context. Render dashboards. |

When you respect this split:
- **The math is reproducible.** Same data + same skill call → same answer, always. The randomness in RF is seeded.
- **The reasoning is auditable.** Every agent's interpretation is grounded in JSON numbers that came from a specific skill call. You can re-run the skill yourself and verify.
- **The context window stays small.** Agents never load 50 MB of deal data; they read 2-3 sentence summaries plus targeted artifact files when needed.

When you violate this split — for example, by asking the LLM to do arithmetic on lists, or by writing a Python skill that "decides which features are important" — the system degrades into the slop both pieces were designed to avoid.

---

## 3. Three-layer architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│   USER PROMPT (Claude Code chat)                                     │
│   e.g. "what predicts S0→S1 conversion in our main pipeline?"        │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│   LAYER 3 — AGENT BRAINS  (.claude/agents/*.md)                      │
│                                                                      │
│      ┌────────────────────┐                                          │
│      │ gtm-orchestrator   │   ◄── single entry point                 │
│      └────────────────────┘                                          │
│              │                                                       │
│              ├─► pipeline-progression-analyst                        │
│              ├─► program-attribution-analyst                         │
│              ├─► signal-combination-analyst                          │
│              ├─► trend-intelligence-analyst                          │
│              └─► icp-synthesis-analyst                               │
│                                                                      │
│   Each agent is a markdown file with frontmatter (name, tools, model)│
│   and a system prompt describing role, instructions, output format,  │
│   final-instructions guardrails, and skill-invocation patterns.      │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                  invokes via Bash (python -m skills.…)
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│   LAYER 2 — REUSABLE SKILLS  (skills/)                               │
│                                                                      │
│      ┌─────────────────────────┐    ┌──────────────────────────┐    │
│      │ skills/hubspot/         │    │ skills/python/           │    │
│      │   8 API connectors      │    │   12 statistical skills  │    │
│      │   client.py (shared)    │    │   _shared.py             │    │
│      └─────────────────────────┘    └──────────────────────────┘    │
│                          │                       │                   │
│                          └───┬───────────────────┘                   │
│                              │                                       │
│                              ▼                                       │
│                ┌──────────────────────────┐                          │
│                │ skills/common/           │                          │
│                │   io_contract.py         │                          │
│                │   manifest.py            │                          │
│                └──────────────────────────┘                          │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                  │
                       reads/writes parquet+JSON
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│   LAYER 1 — DATA + EXTERNAL APIS                                     │
│                                                                      │
│   HubSpot CRM API (live, authenticated via .env)                     │
│      │                                                               │
│      └─► data/raw/        ← parquet from each hs_pull_* call         │
│          data/features/   ← parquet + manifest from py_feature_      │
│                              engineering                              │
│          data/results/    ← JSON from every analytical skill         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

The three layers communicate exclusively through:
- Agents → Skills: command-line invocation via `Bash` tool (`python -m skills.hubspot.hs_pull_deals '<json>'`)
- Skills → Agents: a single line of compact JSON on stdout
- Skills ↔ Skills: parquet files on disk, keyed by a `run_id` (manifest pattern)

There are no shared in-memory data structures. Each skill invocation is a fresh process. This makes the system resumable: if an agent or skill crashes mid-audit, the parquet artifacts on disk let you pick up where you left off.

---

## 4. Layer 1 — HubSpot connector skills

`skills/hubspot/` contains 8 standalone Python modules plus one shared client. Each module is a single-purpose wrapper over one HubSpot CRM endpoint family.

```
skills/hubspot/
├── client.py                       — shared HubSpotClient: auth, pagination, retry
├── _shared.py                      — common pull-loop for CRM objects
├── hs_pull_custom_properties.py    — schema introspection
├── hs_pull_deals.py
├── hs_pull_contacts.py
├── hs_pull_companies.py
├── hs_pull_associations.py
├── hs_pull_engagements.py
├── hs_pull_marketing_events.py
└── hs_pull_email_stats.py
```

### The shared HubSpotClient

`skills/hubspot/client.py` exposes one class, `HubSpotClient`, that handles everything routine:

- **Authentication** — reads `HUBSPOT_TOKEN` from `.env` (via `python-dotenv`) and injects `Authorization: Bearer <token>` on every request.
- **Pagination** — two helpers:
  - `paginate_list()` for the unfiltered `GET /crm/v3/objects/{type}` endpoint (no row limit).
  - `paginate_search()` for the filtered `POST /crm/v3/objects/{type}/search` endpoint (capped at 10,000 results per query by HubSpot).
- **Rate limit / retry** — handles 429 responses by sleeping for `Retry-After` seconds; retries 5xx with exponential backoff; max 5 attempts.
- **Association resolution** — `associations_batch_read(from_type, to_type, ids)` walks the v4 batch association endpoint in 1,000-id chunks.
- **Batch object reads** — `batch_read(type, ids, properties)` reads up to 100 objects per request via `POST /crm/v3/objects/{type}/batch/read`.
- **Filter translation** — `build_filter_groups()` converts a flat list of `{property, operator, value}` filters into HubSpot's nested `filterGroups` schema, with operator-synonym normalization (`>=` → `GTE`, etc.) and special handling for `BETWEEN`, `IN`, `HAS_PROPERTY`.

The client never returns raw HTTP responses. It returns parsed Python dicts. Errors raise `HubSpotError` with the status code and a 500-char tail of the response body for triage.

### Each skill is a thin wrapper

Most skills are 5–15 lines of orchestration around the client:

```python
# skills/hubspot/hs_pull_deals.py (essence)
from ._shared import run_object_pull
from ..common.io_contract import read_params

DEFAULT_PROPERTIES = ["dealname", "pipeline", "dealstage", "amount", ...]

if __name__ == "__main__":
    params = read_params()
    run_object_pull(object_type="deals", params=params,
                    default_properties=DEFAULT_PROPERTIES)
```

`run_object_pull` (in `_shared.py`) does the same thing for every CRM object type:

1. Take `properties`, `filters`, `associations`, `limit` from params.
2. If `filters` is non-empty → use `paginate_search` (filtering requires the search endpoint).
3. If no filters → use `paginate_list` (unlimited pagination, faster).
4. Flatten each result via `flatten()` → `{id: <id>, ...properties}` with associations joined as `assoc_<type>_ids`.
5. Write a parquet to `data/raw/<object>_<hash>.parquet`, where `<hash>` is a 10-char sha256 of the query params (so identical queries hit the same file).
6. Emit a compact JSON envelope to stdout.

### Skills that don't fit the standard pattern

Three skills do more than a plain object pull:

- **`hs_pull_associations`** — takes a `from_type`, `to_type`, and either explicit `ids` or `ids_from` (a parquet path). Calls `associations_batch_read`. Emits a tall dataframe `{from_id, to_id}`.

- **`hs_pull_engagements`** — engagement objects in HubSpot are split across 5 sub-types (calls, emails, meetings, notes, tasks). This skill loops across them, optionally filters by `associated_type` + `associated_ids`, and emits a unified parquet with an `engagement_type` column.

- **`hs_pull_marketing_events`** — pulls events, then for each event optionally walks the per-state attendance endpoints (`/marketing/v3/marketing-events/{id}/attendance/{state}/read`) for registered, attended, cancelled, no-show. Saves events and attendance as separate parquets.

- **`hs_pull_email_stats`** — has two modes: `contact_rollup` (faster, uses contact-level rollup properties) and `event_stream` (slower, walks the legacy `/email/public/v1/events` endpoint).

### Schema introspection — the single most important skill

`hs_pull_custom_properties` is what makes every other skill portable across HubSpot instances:

```bash
python -m skills.hubspot.hs_pull_custom_properties \
  '{"object_type":"deals","filter_name":"stage"}'
```

This returns every property on the `deals` object whose name or label contains "stage". HubSpot stage-date properties differ per instance: standard `hs_date_entered_*`, v2 `hs_v2_date_entered_*`, custom numeric IDs (e.g. `hs_v2_date_entered_2344069311`). Agents are **instructed** to run this first whenever they encounter a new instance or new question, before assuming property names. Without this, the system would silently fail on any non-default HubSpot configuration.

---

## 5. Layer 2 — Python statistical skills

`skills/python/` contains 12 single-purpose statistical skills plus one foundation (`py_feature_engineering`).

```
skills/python/
├── _shared.py                  — load_run(run_id) → (df, manifest, target)
├── py_feature_engineering.py   — FOUNDATION — every downstream skill depends on this
├── py_mann_whitney.py
├── py_categorical_conversion.py
├── py_random_forest.py
├── py_spearman.py
├── py_stage_conversion.py
├── py_combination_analysis.py
├── py_interaction_effects.py
├── py_kmeans_cluster.py
├── py_logistic_regression.py
├── py_trend_analysis.py
└── py_cohort_analysis.py
```

### py_feature_engineering — the foundation

This skill must run first. It transforms a raw HubSpot pull into a clean feature matrix AND a **column manifest** that every downstream skill reads. The manifest is what makes the system work without the agent having to enumerate column names in every call.

Inputs:
```json
{
  "input": "data/raw/contacts_abc123.parquet",
  "run_id": "audit_q2_2026",
  "target": "has_opp",
  "target_rule": {"expr": "num_associated_deals >= 1"},
  "multi_value_fields": ["events_attended", "webinars_attended"],
  "exclude_values": {"events_attended": ["test_event"]},
  "extra_excluded": ["num_associated_deals"]
}
```

What it does, in order:

1. **Load + dedupe.** Reads one or more parquet files, dedupes on `id` (or whatever `dedupe_on` says), logs the drop count.
2. **Coerce numerics.** Any object-dtype column whose values look numeric becomes numeric.
3. **Coerce dates.** Any object-dtype column whose first 50 values match `\d{4}-\d{2}-\d{2}` becomes UTC datetime.
4. **Null-rate warnings.** Any column with >50% null gets a warning in metadata.
5. **Multi-value field parsing.** For each `multi_value_field`, split on `;` `,` `|`, drop empty/test-flagged values, then emit:
   - `{field}_count` (number of parsed values per row)
   - `has_any_{field}` (binary 0/1 rollup)
   - `{field}__{value}` per unique value (binary 0/1 — the per-program flags)
6. **Ordinal encoding.** `seniority`, `job_function`, `revenue_range` columns get `*_ord` numeric ranks using a built-in mapping (`director→5`, `vp→6`, `executive→7`, `chief/ceo/cto→8`, etc.). Custom mapping can override via `seniority_map` param.
7. **Date-derived features.** For each datetime column, emit `days_since_{col}` and `{col}_month_idx` (year * 12 + month, for trend work). The skill is timezone-aware: it computes `now` in the same tz as the column being subtracted.
8. **Target construction.** If `target_rule` is provided, evaluate it (`expr` is a pandas `df.eval` string; `from_date` derives target=1 where a date is non-null). Otherwise, if `target` doesn't already exist and `num_associated_deals` does, derive `has_deal = num_associated_deals >= 1` and warn.
9. **NaN imputation.** Numeric columns get column-median fill.
10. **Save outputs:**
    - `data/features/<run_id>.parquet` — the cleaned feature matrix
    - `data/features/<run_id>_manifest.json` — column classification

### The column manifest

After feature engineering, every column lives in exactly one bucket:

```json
{
  "run_id": "audit_q2_2026",
  "target": "has_opp",
  "features_path": "data/features/audit_q2_2026.parquet",
  "n_records": 10024,
  "baseline_rate": 0.0110,

  "id":           ["id", "contact_id", "deal_id"],
  "target":       ["has_opp"],
  "numeric":      ["hs_analytics_num_visits", "hs_email_open", ...],
  "ordinal":      ["seniority_ord", "revenue_range_ord"],
  "categorical":  ["country", "industry", "hs_analytics_source", ...],
  "binary":       ["source_organic", "source_paid_social", ...],
  "rollup":       ["has_any_events_attended", "has_any_webinars_attended", ...],
  "date":         ["createdate", "first_conversion_date", ...],
  "date_derived": ["days_since_createdate", "createdate_month_idx", ...],
  "excluded":     ["meeting_booked", "closedate", "num_associated_deals", ...]
}
```

`skills/common/manifest.py` provides helpers (`numeric_columns()`, `categorical_columns()`, `binary_columns()`, `feature_columns()`) that downstream skills use to **auto-resolve their inputs**. An agent calling `py_mann_whitney` doesn't pass column names — the skill reads the manifest and tests every numeric column against the target. This is the single biggest reason agent reasoning doesn't get bogged down in column-enumeration overhead.

### The 11 analytical skills

Each follows the same input/output contract (see §7). Each reads the manifest via `_shared.load_run(run_id)`.

| Skill | What it does | Output highlights |
|---|---|---|
| `py_mann_whitney` | Mann-Whitney U batch across all numeric features vs binary target | Top-significant features by p-value; flags inverted medians on volume metrics (mass programs reaching unqualified audiences) |
| `py_categorical_conversion` | Per-category conversion rate + chi² + lift vs baseline for every categorical and binary column | Top positive segments + top negative segments; flags `n<15` as directional |
| `py_random_forest` | Balanced RF with 5-fold stratified CV; auto-drops zero-variance features | AUC, F1, ranked feature importance; flags AUC>0.95 as likely leakage |
| `py_spearman` | Single-pair OR batch rank correlation vs a reference column | Sorted by `\|r\|`; flags meaningful (`\|r\|>=0.3, p<0.05`) |
| `py_stage_conversion` | Single-pair OR auto-detected stage matrix; computes rate + median days-in-stage; drops bad date sequences | Per-transition n_src, n_destination, rate, median_days, bad_dates_dropped |
| `py_combination_analysis` | Pairwise + triple binary-combination conversion rates | Top combos with lift vs baseline |
| `py_interaction_effects` | 2×2 interaction tables; additive-expected vs observed | SYNERGY flag (Δ>+10pp) / SUPPRESSION flag (Δ<−5pp) |
| `py_kmeans_cluster` | KMeans for k=3,4,5; selects best k by silhouette; profiles each cluster | Cluster sizes, deal rates, top defining traits per cluster, best/worst gap ratio |
| `py_logistic_regression` | L2 logistic (C=0.1, balanced); optional pairwise interaction terms | Top 10 positive + top 10 negative coefficients |
| `py_trend_analysis` | Monthly conversion rate + Spearman vs month index; auto-detects date column | Monthly table, direction, meaningful flag, recency-bias warning |
| `py_cohort_analysis` | Tercile cohort split (auto or explicit boundaries); chi² across cohorts; feature distribution per cohort | Cohort sizes/rates, profile shifts |

> **Beyond these 11:** the Marketing Mix Model extension adds four more common/Python skills (`parse_budget_workbook`, `load_spend`, `py_mmm_features`, `py_marketing_mix_model`) that operate on **aggregate time series** rather than the contact feature matrix — they are *not* manifest-driven and do not use `load_run`. See §17.

---

## 6. Layer 3 — Agent brains

`.claude/agents/` contains 7 markdown files. Each is a Claude Code subagent definition.

### Subagent file format

```markdown
---
name: pipeline-progression-analyst
description: |
  Use this agent for any question about what predicts whether a deal
  moves from one pipeline stage to the next...
tools: Bash, Read, Grep, Glob, Write
model: sonnet
---

# ⚠️ MUST-follow rules
[short summary of analysis_rules.md + link to full file]

# Role
[role objective — what this agent is for]

# Skills available
[concrete bash invocation patterns for each skill]

# Instructions
[numbered recipe of the analyst's standard workflow]

# Output format
[the structured briefing the agent must produce]

# Final instructions
[guardrails: small-n discipline, correlation-not-causation, etc.]

# Statistical interpretation reference
[the cheat sheet of thresholds]
```

The YAML frontmatter is read by Claude Code at session start. The body becomes the agent's system prompt. When another agent (typically the orchestrator) invokes this subagent via the `Task` tool, Claude Code spawns a new agent with this system prompt and the listed tools, receives the agent's final response, and returns it.

### The seven agents

| Agent | Role | Domain |
|---|---|---|
| `gtm-orchestrator` | Master coordinator — single entry point for any user prompt | Routing, decomposition, cross-worker synthesis |
| `pipeline-progression-analyst` | Stage-to-stage conversion (S0→S1, S1→S2, …); rep-execution vs ICP signals; velocity | Deal level |
| `program-attribution-analyst` | Marketing program effectiveness; per-program conversion + lift vs baseline; nurture/Academy/event analysis | Contact level |
| `signal-combination-analyst` | Pairwise + triple combos; SYNERGY/SUPPRESSION interaction detection; KMeans archetypes | Contact level |
| `trend-intelligence-analyst` | Monthly trends; per-program cohort grids; event-window impact; recency-bias-aware | Contact level (temporal) |
| `icp-synthesis-analyst` | Cross-stage ICP: which firmographic attributes hold up at every gate, not just S0 | Deal + contact joined |
| `marketing-mix-analyst` | Top-down Bayesian MMM: spend→pipeline by channel; adstock + saturation; budget allocation, ROI, response curves. *Complements*, never replaces, program-attribution (§17) | Aggregate time series |

Each specialist's body contains:
- The MUST-follow rule block (links to `docs/analysis_rules.md`)
- A numbered instruction recipe (the analyst's "default playbook")
- The output format the user will see
- Guardrails (e.g., never recommend cutting a program based on conversion rate alone if it serves awareness)

The orchestrator additionally has the `Task` tool, which is how it dispatches to specialists.

---

## 7. The skill contract

Every skill — HubSpot or Python — implements this exact I/O contract. It's enforced by `skills/common/io_contract.py`.

### Invocation

```bash
# Argv form (preferred)
python -m skills.<group>.<skill_name> '<json-params-as-single-arg>'

# Stdin form
echo '<json-params>' | python -m skills.<group>.<skill_name>
```

`read_params()` checks argv first, falls back to stdin. Invalid JSON → error envelope and exit code 2.

### Output envelope (stdout)

A single line of compact JSON:

```json
{
  "results": {
    "key1": "value1",
    "key2": ["compact", "structured", "result"]
  },
  "summary": "Pulled 393 deals from default pipeline with 27 properties. Cached at data/raw/deals_a47cc0a084.parquet.",
  "metadata": {
    "n_records": 393,
    "n_features": 27,
    "warnings": ["Column X is 82% null — use with caution."],
    "artifacts": {
      "parquet": "data/raw/deals_a47cc0a084.parquet"
    }
  }
}
```

Fields:
- `results` — the structured analytical output. Small enough to fit in agent context. Top-10 lists, not full dataframes.
- `summary` — 2–3 sentences of plain English the agent can quote.
- `metadata.n_records`, `metadata.n_features` — basic shape.
- `metadata.warnings` — anything the skill detected that the agent should know about (high null rates, dropped records, leakage flag, etc.).
- `metadata.artifacts` — paths to disk-cached artifacts (parquet, JSON) that contain the full output if the agent needs to drill in.

### Error envelope

If anything goes wrong:

```json
{
  "results": null,
  "summary": "ERROR: Manifest not found for run_id=audit_q2. Run py_feature_engineering first.",
  "metadata": {
    "n_records": 0,
    "n_features": 0,
    "warnings": [],
    "error": "FileNotFoundError: ..."
  }
}
```

The agent reads `summary`, sees "ERROR:", and either fixes the missing dependency or reports the failure upstream.

### Why this contract matters

- **Agents see ~500 bytes per skill call.** They never load 50 MB of deal data into context.
- **Full results are recoverable.** When an agent needs granular numbers (e.g., to write a finding citation), it uses the `Read` tool on the artifact path.
- **Skills are composable.** Output of one skill (a parquet path) becomes input to another (`{"input": "<path>"}`).
- **Skills are independently runnable.** You can test any skill from your terminal with no agent involved.

---

## 8. The manifest-driven design

The single most important design decision in the system.

### The problem it solves

A naive design would have each analytical skill take a list of features as input:

```bash
python -m skills.python.py_mann_whitney \
  '{"data":"path", "target":"has_opp",
    "features":["col1","col2","col3","col4",...100 more...]}'
```

Two problems with this:
1. The agent has to know every column name in the data — expensive to fit in context, fragile to schema changes.
2. The agent has to classify columns as numeric/categorical/binary before calling the right skill — exactly the kind of bookkeeping it's bad at.

### The solution

`py_feature_engineering` runs first, classifies every column, and writes a manifest. Every downstream skill reads the manifest and auto-resolves which features to analyze.

```bash
# First, FE.
python -m skills.python.py_feature_engineering \
  '{"input": "data/raw/contacts_abc.parquet", "run_id": "audit_q2", "target": "has_opp"}'

# Now any downstream skill just takes the run_id.
python -m skills.python.py_mann_whitney   '{"run_id": "audit_q2"}'
python -m skills.python.py_categorical_conversion '{"run_id": "audit_q2", "min_n": 15}'
python -m skills.python.py_random_forest  '{"run_id": "audit_q2"}'
```

`py_mann_whitney` auto-runs against every column in `manifest["numeric"]`. `py_categorical_conversion` auto-runs against `manifest["categorical"] + manifest["binary"] + manifest["rollup"]`. `py_random_forest` auto-resolves the full feature matrix via `feature_columns(manifest)` which is `numeric + ordinal + date_derived + binary + rollup`.

### Override path

If an agent needs to be specific — for example, only test certain columns — it passes them explicitly:

```bash
python -m skills.python.py_categorical_conversion \
  '{"run_id":"audit_q2", "columns":["country","industry","hs_role"], "min_n":15}'
```

When `columns` is provided, the manifest auto-resolution is skipped. Same for `features` in MW and RF.

### Contamination columns

`skills/common/manifest.py::CONTAMINATION_COLUMNS` blacklists a default set of columns that are downstream consequences of an outcome and would leak the target:

```python
{
  "meeting_booked", "hs_meeting_booked", "demo_completed",
  "sql_created", "mql_created", "opportunity_created",
  "closed_date", "closedate", "hs_closedate",
}
```

These are excluded from `manifest["numeric"]/binary/categorical` automatically. Agents extend the blacklist per-analysis with `extra_excluded` — e.g., when running an S1→S2 analysis, the agent excludes every later stage's date columns + their `_month_idx` and `days_since_*` derivatives.

(One known gap: when an agent excludes a date column, FE doesn't automatically also exclude that column's derived `_month_idx` and `days_since_*` columns. Agents must list them explicitly. See §15.)

---

## 9. The orchestrator

`gtm-orchestrator.md` is the single user-facing entry point.

### What the orchestrator does

1. **Interprets the user's prompt.** Identifies the core analytical question, scope, and desired output depth. States its interpretation in one sentence before doing anything.
2. **Declares a routing plan.** Tells the user which workers are being dispatched, which skills are being called directly, and in what order.
3. **Routes:**
   - Single-domain question → dispatch one specialist via `Task` tool
   - Multi-domain question → dispatch multiple specialists (parallel if independent, sequential with context-passing if dependent)
   - Full audit → dispatch all 5 specialists in canonical order, then synthesize
   - Data retrieval only → call HubSpot skill directly
   - Single statistical test → call the API skill + Python skill directly
   - Ambiguous request → ask ONE scoping question, then route
4. **Sanity-checks specialist results.** If a specialist returns a result with an implausible baseline (e.g., 47% contact-level opp rate when deal count is small), the orchestrator rejects it and re-runs with corrections rather than forwarding inflated numbers.
5. **Synthesizes across workers.** After all specialists complete, produces a cross-worker analysis: top 3–5 highest-confidence findings, recommended actions, open questions. Single-worker findings are presented as directional; cross-worker-consistent findings are presented as high-confidence.

### The full-audit canonical order

1. `pipeline-progression-analyst` — pulls deal-level data; defines the deal universe
2. `program-attribution-analyst` — pulls contact-level data; defines marketing performance
3. `signal-combination-analyst` — reuses contact data; finds combos/synergies/archetypes
4. `trend-intelligence-analyst` — same contact data; temporal cuts
5. `icp-synthesis-analyst` — synthesizes deal + contact for cross-stage ICP

Each later worker is given a summary of earlier findings as context — NOT the raw dataframes. They share data on disk via `run_id`, not in agent memory.

### Why a separate orchestrator exists

If the orchestrator were merged with the user-facing main session, every chat would need to load all 5 specialist system prompts into context. Separating it lets:
- The main session stay lean
- Each specialist run in its own subagent with its own context window
- Specialists be invoked individually (without the full orchestrator overhead) when the question is narrow

---

## 10. Caching strategy

Every skill writes its output to disk under `data/`. Subsequent calls can re-use the same artifacts without re-pulling or re-computing.

### Three caches

```
data/
├── raw/         — HubSpot API pulls keyed by query hash
│                  e.g. deals_a47cc0a084.parquet
├── features/    — Feature-engineered output keyed by run_id
│                  e.g. audit_q2_2026.parquet
│                       audit_q2_2026_manifest.json
└── results/     — Analytical output keyed by run_id + skill
                   e.g. audit_q2_2026_mann_whitney.json
                        audit_q2_2026_random_forest.json
```

### Cache keying

- **raw/** — file name = `<object_type>_<hash>.parquet`. `<hash>` is the first 10 chars of a sha256 of the query params (properties, filters, associations, limit). Identical queries from any source hit the same cache file.
- **features/** — file name = `<run_id>.parquet` + `<run_id>_manifest.json`. The agent picks the run_id, typically encoding scope + date window (e.g. `audit_o25a26_mkt`).
- **results/** — file name = `<run_id>_<skill>.json`. e.g. `audit_q2_2026_categorical_conversion.json`.

### When to reuse vs re-pull

| Situation | Action |
|---|---|
| Same session, same scope, different question on already-pulled data | Reuse the existing run_id; call new analytical skills against it |
| Same scope but different target | New run_id, reuse the raw parquet (pass it as `input` to a fresh FE) |
| Different scope (date range, pipeline) | New raw pull, new run_id |
| >4 hours since pull | Consider re-pulling (HubSpot data may have shifted) |
| Demonstrating reproducibility / debug | Always reuse to confirm the same numbers come out |

The orchestrator is instructed to check `data/results/` before re-running analyses to answer follow-up questions.

### What's NOT cached

- Agent reasoning. Every agent invocation is a fresh subagent with no memory of prior runs.
- Cross-session state. The `data/` directory persists across Claude Code sessions, but the agent context does not.

---

## 11. Analytical rules engine

`docs/analysis_rules.md` is the authoritative source for target definitions and cohort construction. Every contact-level analysis must pass these rules. The rules are referenced from `AGENTS.md` (auto-loaded by Claude Code into every session) AND from each specialist's MUST-follow block.

### The rules (paraphrased)

1. **Opportunity target = INTERSECTION** of `lifecyclestage ∈ {opportunity, salesqualifiedlead, customer}` AND `contact ∈ windowed deal-association set`. **Never** union. **Never** include custom numeric stage IDs (e.g. `2265264367`) — those are typically marketing-workflow stages like "Sales Accepted Lead" and inflate rates 20–30×.
2. **Trend analysis must segment by `recent_conversion_event_name` bucket.** Global monthly trends hide composition shifts. Every contact-level trend output must produce a month × program-bucket grid.
3. **Sanity-check every rate.** `positive_count > 2 × n_deals_in_window` ⇒ the target is wrong. Stop and re-examine.
4. **Custom numeric stage IDs must be resolved before use** via `hs_pull_custom_properties`.
5. **`source_offline` is NOT a marketing channel.** Exclude from marketing attribution. Marketing cohort = `recent_conversion_date HAS_PROPERTY AND source_offline = 0`.
6. **State the target definition** at the top of every analytical output.

### Why these rules exist

Each rule was added in response to a specific past failure:

- Rule 1: a union target produced a 46.67% "opportunity rate" against 10,951 contacts in a 502-deal window. Actual rate under intersection was 1.72%. Reported headline findings were 27× inflated.
- Rule 2: aggregate monthly trend was framed as "conversion rate collapsed 4.1% → 0.6%" when the real story was 70% composition shift (LinkedIn Lead Gen growing to 90% of volume) and 30% within-program quality decline.
- Rule 3: the sanity check would have caught Rule 1's mistake immediately. It's the cheapest possible guardrail.
- Rule 4: custom stage IDs like `2265264367` are easy to assume mean "opportunity" given they appear alongside `opportunity`, `customer`, `salesqualifiedlead` in lifecyclestage data. They don't.
- Rule 5: HubSpot's `OFFLINE` source label captures rep-prospected lists AND event-attendee imports, not inbound marketing. Including OFFLINE in marketing attribution credits sales work to marketing.
- Rule 6: makes every output self-auditable.

### How enforcement works

There's no automated checker — enforcement is via the agent system prompts. Every analyst's MUST-follow block links to `docs/analysis_rules.md`. The orchestrator's MUST-follow block tells it to **reject** any specialist output with a baseline that fails Rule 3 rather than forwarding it to the user.

---

## 12. Memory persistence

Two feedback memories live at:

```
~/.claude/projects/-Users-ameyadeshmukh-Documents-pipelineanalysis/memory/
├── MEMORY.md                          — index (links to topical files)
├── project_gtm_intelligence.md        — what this project is, why it exists
├── feedback_opportunity_target.md     — Rule 1 + the 2026-04-21 incident
└── feedback_marketing_scope.md        — Rule 5 + why OFFLINE is excluded
```

`MEMORY.md` is auto-loaded into every Claude Code session in this project directory. The links pull in the specifics on demand. This is how the system survives across sessions — even a brand-new Claude Code chat in this repo starts with the failure modes already encoded in context.

The feedback memories were created **after** specific mistakes, with the offending incident cited verbatim, so the same mistake can't quietly recur.

---

## 13. End-to-end walkthrough: "run a full GTM audit"

Concrete trace of what happens when a user types this prompt.

### Step 0 — session boot

When the Claude Code session starts in this directory:
- `AGENTS.md` auto-loads → top-level project context + rule summary + repo layout
- `MEMORY.md` + linked feedback files auto-load → past-incident discipline
- `.claude/agents/*.md` frontmatter loaded → 6 subagents discovered and registered

### Step 1 — orchestrator receives the prompt

The orchestrator agent picks up "run a full GTM audit" because that's exactly what its description matches.

### Step 2 — orchestrator declares the plan

```
I will run a full GTM audit by dispatching all 5 specialists in canonical
order: pipeline-progression → program-attribution → signal-combination →
trend-intelligence → ICP-synthesis. Each will be passed the prior workers'
summarized findings. I'll synthesize at the end. Pulling deals + contacts
for the window first.
```

### Step 3 — pipeline-progression-analyst (dispatched via Task tool)

The pipeline-progression-analyst subagent runs in a fresh Claude Code subagent context:

```bash
# 1. Schema check — figure out which stage-date properties exist
python -m skills.hubspot.hs_pull_custom_properties \
  '{"object_type":"deals","filter_name":"stage"}'

# 2. Pull deals in window
python -m skills.hubspot.hs_pull_deals '{
  "properties":["dealname","pipeline","dealstage","amount",
                "hs_v2_date_entered_appointmentscheduled",
                "hs_v2_date_entered_qualifiedtobuy", ...],
  "filters":[{"property":"pipeline","operator":"EQ","value":"default"},
             {"property":"createdate","operator":"GTE","value":"2026-04-01"}],
  "limit":"all"
}'

# 3. Resolve associations
python -m skills.hubspot.hs_pull_associations \
  '{"from_type":"deals","to_type":"contacts",
    "ids_from":"data/raw/deals_a47cc0a084.parquet"}'

# 4. Batch-read contacts for enrichment
python -m skills.hubspot.hs_pull_contacts \
  '{"filters":[{"property":"hs_object_id","operator":"IN","values":[...]}], ...}'

# 5. Feature engineering with target=reached_s1
python -m skills.python.py_feature_engineering '{
  "input":"data/raw/deals_a47cc0a084.parquet",
  "run_id":"audit_q2_deals",
  "target":"reached_s1",
  "target_rule":{"from_date":"hs_v2_date_entered_appointmentscheduled",
                 "name":"reached_s1"},
  "extra_excluded":[...]
}'

# 6. Stage chain
python -m skills.python.py_stage_conversion '{"run_id":"audit_q2_deals","auto_stage_matrix":true}'

# 7. Categorical predictors
python -m skills.python.py_categorical_conversion \
  '{"run_id":"audit_q2_deals","target":"reached_s1","min_n":10}'

# 8. RF for feature importance
python -m skills.python.py_random_forest '{"run_id":"audit_q2_deals"}'

# 9. Mann-Whitney on numerics
python -m skills.python.py_mann_whitney '{"run_id":"audit_q2_deals"}'
```

The analyst then writes its briefing (stage chain table, top S0→S1 predictors, owner ranking, velocity) and returns it to the orchestrator.

### Step 4 — orchestrator passes condensed context to next analyst

The orchestrator does NOT forward the full briefing or the raw dataframes. It extracts 5–10 bullet points (e.g. "owner X has 0% S1→S2 across n=96 deals; IMPORT source converts at 17% vs 42% baseline; …") and passes them as context to the program-attribution-analyst.

### Step 5 — program-attribution-analyst runs

Same pattern: schema check → pull contacts (filtered to marketing cohort per Rule 5) → run_id = `audit_q2_contacts` → FE → categorical_conversion on program bucket columns → write briefing. Reuses the deal-association parquet from step 3 (cached on disk).

### Steps 6–8 — signal-combination, trend-intelligence, ICP-synthesis

Each follows the same pattern with their own skill recipes. ICP-synthesis is given the accumulated context from all 4 prior workers.

### Step 9 — orchestrator synthesizes

The orchestrator reads the 5 returned briefings, identifies cross-worker patterns (e.g., owner X appears as the worst owner in pipeline-progression AND has the lowest signal-combination scores AND shows up in the worst trend cohort), and writes the final synthesis: top 3–5 findings, recommended actions, open questions.

### Step 10 — user sees the synthesis

The user sees the orchestrator's final response. The 5 specialist briefings are available in the chat history but compressed; the full raw data is on disk in `data/`.

---

## 14. Extension patterns

### Add a new HubSpot skill

1. Create `skills/hubspot/hs_pull_<X>.py`.
2. Import the shared client + `read_params` + `emit`.
3. Either use `run_object_pull` (if it's a standard CRM object) or write custom logic.
4. Save raw output to `data/raw/<X>_<hash>.parquet`.
5. Emit the standard envelope.
6. Document in `docs/skills.md` and add invocation example to relevant agent `.md` files.

### Add a new Python statistical skill

1. Create `skills/python/py_<X>.py`.
2. Import `from .._shared import load_run` to get `(df, manifest, target)` for a given `run_id`.
3. Use `manifest` helpers to auto-resolve features.
4. Compute results.
5. Save full output to `data/results/<run_id>_<X>.json`.
6. Emit envelope with `summary`, top-10 in `results`, artifact path in metadata.
7. Document in `docs/skills.md` + add to relevant agents.

### Add a new agent

1. Create `.claude/agents/<name>.md`.
2. Frontmatter: `name`, `description`, `tools`, `model` (sonnet usually).
3. Body: MUST-follow rules block (link to analysis_rules) → Role → Skills available → Instructions → Output format → Final instructions → Statistical interpretation reference.
4. Add to orchestrator's dispatch list if it should participate in full audits.

### Add a new rule

1. Add it to `docs/analysis_rules.md` with rationale + code pattern + the past incident that motivated it.
2. Add the one-line summary to AGENTS.md's "⚠️ Analytical rules" block.
3. Reference it from the relevant agent's MUST-follow block.
4. Save a feedback memory in `~/.claude/projects/-Users-ameyadeshmukh-Documents-pipelineanalysis/memory/feedback_<topic>.md`.
5. Link the new memory from `MEMORY.md`.

---

## 15. Known limits and technical debt

### Hard limits (HubSpot API)

- **Search API caps at 10,000 results.** When a filtered query would return more, the system splits the window in half and re-pulls (or accepts the cap with a documented warning).
- **Association batch read max 1,000 IDs per request.** The client chunks automatically.
- **Object batch read max 100 IDs per request.** Same.
- **Rate limit:** HubSpot enforces 429s; the client respects `Retry-After`. Heavy pulls (5,000+ contacts) can take 30–60 seconds.

### Known bugs and gaps

- **FE doesn't cascade exclusions to date derivatives.** When an agent excludes a date column via `extra_excluded`, the `_month_idx` and `days_since_*` columns derived from it are NOT auto-excluded. The agent must list them explicitly. This caused a false leakage flag on the first S1→S2 analysis (AUC = 0.994 because `hs_v2_date_entered_qualifiedtobuy_month_idx` leaked the target). Workaround documented in each analyst's instruction block.
- **`SUPPRESSION` false positives on highly collinear pairs.** `py_interaction_effects` computes `additive_expected = min(1.0, rate_a + rate_b - baseline)`. When both signals individually exceed ~70% conversion, `additive_expected` caps at 100%, and any observed value looks like suppression. The skill should suppress the flag when `rate_a + rate_b > 1.2`. Documented in the signal-combination analyst's body.
- **HubSpot trailing-space data quality.** At least one stored enum value in this instance has a trailing space (`"LinkedIn Lead Generation Ad: 6 AI Agents for Marketing "`) that breaks exact-match filters. Filter values must match exactly including trailing whitespace, or the search returns silently empty.
- **The `IN` filter in HubSpot search** appears to silently drop the largest-cardinality value in some cases. Workaround: split into N separate `EQ` queries and merge.
- **`num_associated_deals` is rarely populated.** In this instance it's almost entirely null. Don't use it as the contact-level deal-association signal — use the actual association parquet from `hs_pull_associations`.
- **Lead scoring infrastructure isn't wired up** in this instance. `hubspotscore`, `hs_predictivecontactscore`, `icp_score`, `news_signal_score`, `open_roles_score`, `sec_signal_score` are all zero-non-null on every contact and company pulled. Only `behavioral_lead_score` is populated.
- **Owner records can be archived.** A `hubspot_owner_id` referenced on a deal may 404 when looked up via `/crm/v3/owners/<id>`. Code handles this with a fallback.
- **No automated tests.** Skills have been smoke-tested end-to-end with synthetic data; no formal test suite yet.

### Marketing Mix Model limits (§17)

- **No spend data in HubSpot.** The MMM requires marketing spend by channel by period from an *external* source (the budget workbook). Without it the model cannot run — engagement counts are not a substitute for dollars.
- **Thin, monthly data.** This instance has ~17 monthly periods, which supports ~4 identifiable channel groups. Beyond that the model is under-identified: per-channel contributions collapse toward their priors and every credible interval includes zero. `load_spend` and `py_mmm_features` warn on this; do not present under-identified channel ROI as fact.
- **Monthly granularity hides short carryover.** Adstock half-lives shorter than a month (branded search, retargeting) are invisible at monthly resolution; weekly spend is needed to estimate them.
- **Observational, not causal.** MMM output is regularized correlation until a lift experiment (geo or account holdout) calibrates it. Always-on channels have their *level* confounded with the baseline; only their *variation* is identified.
- **PyMC backend needs Python 3.11+.** The default `laplace` and `metropolis` backends run anywhere (NumPy/SciPy); the NUTS `pymc` backend is unavailable on the local Python 3.9 and is intended for the Railway 3.11 image.

### Things the system doesn't do (intentional)

- **No dashboards or charts in the analytics layer.** The skills and agents emit markdown + structured CSV/parquet/JSON; the interpretation is in the text. The optional `webapp/` consumption layer (§18) renders Plotly charts over those same artifacts but adds no new analytics.
- **No CRM writes.** All HubSpot calls are read-only. The system never updates deals, contacts, or properties.
- **No live monitoring or alerts.** Each user prompt is an ad-hoc pull. There's no scheduled job or watchlist.
- **No causal inference.** Every claim is correlational. Agents are forbidden from using causal language unless a clean natural experiment exists in the data.
- **No slide-deck generation.** If you want a deck, take the synthesis markdown and use it as the source.

---

## 16. Why these choices

### Why Claude Code subagents instead of the EverWorker hosted platform?

- Subagents are scoped: each runs in its own context with its own tool subset
- Project-level memory persists across sessions in a known directory
- The `.claude/agents/*.md` format is human-editable and version-controllable
- No vendor lock-in beyond Claude itself
- Free-tier compatible

### Why parquet instead of CSV for raw pulls?

- Preserves dtypes (datetime, int, float, string) without re-parsing
- 5–10× smaller on disk
- pandas reads/writes natively
- Columnar format works well for the "subset of columns to analyze" pattern

### Why one JSON line per skill output?

- Trivial to parse (`json.loads(stdout.strip().split("\n")[-1])`)
- Compact enough to fit in agent context
- Streamable if needed (each line is a complete record)

### Why disk-cached artifacts instead of an in-memory store?

- Survives process crashes
- Survives session restarts
- Inspectable by the user (open the parquet in pandas; open the JSON in any editor)
- Removes the need for a long-lived process or database
- Trivial to clean: `rm -rf data/`

### Why manifest-driven feature classification?

- Removes the LLM from the column-bookkeeping loop
- Makes downstream skills declarative (`{"run_id": "..."}`)
- Centralizes contamination-column exclusion
- Trivial to extend: add a new column type to the classifier in `skills/common/manifest.py`

### Why a separate orchestrator agent?

- Keeps the main session lean (only the orchestrator's system prompt is loaded by default)
- Lets specialists be invoked independently when the question is narrow
- Centralizes cross-worker synthesis logic
- Makes routing decisions auditable (the orchestrator declares its plan before executing)

### Why rules and memory instead of agent prompt-only enforcement?

- Rules live in a versioned file (`docs/analysis_rules.md`) that ships with the repo
- Multiple agents can reference the same authoritative source
- Adding a rule is a single edit + a memory file, not 6 agent edits
- The "past incident" framing in each rule + memory makes the failure mode concrete instead of abstract

---

## 17. Marketing Mix Model (top-down extension)

The six original analysts are **bottom-up**: they read person-level touch history and ask *"which programs/signals appear on the contacts that became opportunities?"* The Marketing Mix Model is the **top-down complement**. It never looks at an individual deal's source. It regresses **one outcome time series** (deals/opps created per period) on **spend-per-channel-per-period**, and infers each channel's contribution from how spend and outcomes co-move over time. This is what lets it capture brand/halo effects that deal-level tags miss — at the cost of never knowing which specific deal a dollar bought. Use it for **budget allocation**; use program-attribution for **program cuts**. (Full reasoning and guardrails live in `.claude/agents/marketing-mix-analyst.md`.)

### The pipeline (four new skills)

```
parse_budget_workbook → load_spend → py_mmm_features → py_marketing_mix_model
   (.numbers → CSV)     (channel       (period×channel    (Bayesian fit +
                         grouping)      design matrix)      decomposition)
```

| Skill | Layer | What it does |
|---|---|---|
| `skills/common/parse_budget_workbook` | ingestion | Parses the Apple **`.numbers`** marketing budget into a tidy long spend CSV (`period, channel, spend`). Reproducible — re-run when the workbook updates. Keeps the workbook's faithful, granular line items. |
| `skills/common/load_spend` | ingestion | Normalizes raw channels into a small set of **modeling groups** (you can't identify 11 channels from ~17 points). Supports explicit **exclusion** of brand/operating spend (map a channel to `null`). Warns on sparsity and unmapped channels. |
| `skills/python/py_mmm_features` | design matrix | Builds the period × channel matrix: outcome = **deals_created** per period (deal-create dates, *not* closed-won), plus controls (trend, annual Fourier seasonality, `active_owners` sales-capacity proxy, optional `eng_volume`). Emits an **MMM manifest** tagging `outcome/media/control/date`. Warns when `params > periods` (**under-identified**). |
| `skills/python/py_marketing_mix_model` | model | Per-channel **adstock** (geometric carryover) + **Hill saturation**, then a Negative-Binomial model that is **additive on the response scale** so `baseline + Σ channel contributions = fitted total exactly`. Bayesian with three backends — `laplace` (default; NumPy/SciPy, runs anywhere), `metropolis` (cross-check, reports Gelman-Rubin R̂), `pymc` (NUTS, needs Py3.11). Outputs contribution + **90% CIs**, marginal ROI, response curves, baseline-vs-incremental split, and diagnostics. |

### Why it breaks the manifest-driven pattern

Every other Python skill reads the contact feature matrix via `load_run(run_id)` (§8). The MMM skills don't — they operate on **aggregate time series** with their own MMM manifest. This is deliberate: MMM is a fundamentally different unit of analysis (period, not contact).

### Canonical configuration (this instance)

- **4 channel groups:** `events` / `paid_social` (LinkedIn + YouTube) / `paid_search` (Google) / `outbound`. Organic-content and martech spend are **excluded** (brand/operating, not demand channels).
- **Outcome:** deals created per **month**.
- The hard rules the analyst enforces: MMM ≠ attribution · deals-created not closed-won · spend is required and external · group to ≤4–6 channels · always quote CIs · name always-on confounding · present as **directional** until a lift experiment calibrates it.

See §15 "Marketing Mix Model limits" for why the per-channel numbers are directional on this data.

---

## 18. Web application

The original system is consumable only from a Claude Code chat. `webapp/` adds a **standalone UI with its own storage** so analyses can be run and insights consumed without chat — runnable locally now, deployable to Railway later.

### How it reuses the system without rewriting it

The web app **invokes every skill unchanged as a subprocess** and parses the standard JSON envelope (§7):

```python
run_skill(module, params) = subprocess.run(
    [sys.executable, "-m", module, json.dumps(params)],
    cwd=<repo root>, env={**os.environ, PIPELINE_DATA_DIR, HUBSPOT_TOKEN})
# → parse the single-line envelope, persist results + metadata.artifacts paths
```

No analytics are reimplemented. This is only possible *because* the skill contract is a clean stateless boundary — the web app is the clearest demonstration of why that contract (§7) matters.

### Stack and components

FastAPI + Jinja2 + **HTMX** + **Plotly** — one process, server-rendered, **no JS build step**. SQLite for storage. `v1` is **deterministic-only**: it runs skills and visualizes results; there is no LLM in the web path (the agentic synthesis layer stays in chat).

| File | Responsibility |
|---|---|
| `webapp/config.py` | Env settings: `PIPELINE_DATA_DIR`, `DB_URL`, `APP_PASSWORD`, `HUBSPOT_TOKEN`, worker count |
| `webapp/db.py` / `models.py` | SQLModel engine + ORM: `Job`, `Run`, `ResultArtifact`, `Dataset`, `SpendUpload`, `MmmModel`, `Scenario` (SQLite at `$PIPELINE_DATA_DIR/app.db`) |
| `webapp/auth.py` | Single shared-password session gate; **disabled when `APP_PASSWORD` is unset** (local). Multi-user accounts deferred — logic isolated so a `User` table slots in later |
| `webapp/jobs.py` | **SQLite-backed background worker** — HubSpot pulls and MMM rebuilds are slow/external, so they run as jobs (no Redis; keeps it single-service for Railway). Jobs persist and survive restarts; HTMX polls `/jobs/{id}` for live status + logs |
| `webapp/skills_registry.py` | Catalog of skills + the canonical multi-skill pipelines (rebuild MMM, run audit) |
| `webapp/routers/{dashboard,mmm,data,audit}.py`, `services/` | Request handlers + result-shaping |

### The four surfaces

1. **MMM dashboard** — flagship. Contribution bars with **90% CI error bars**, response curves with a current-spend marker, baseline-vs-incremental split, spend-vs-deals timeline. Surfaces the honest guardrails (under-identification, CI-includes-0, calibration caveat) as UI annotations rather than hiding them.
2. **Budget what-if planner** — enter a per-channel spend plan; projects incremental deals by reading each channel's fitted **response curve** (no re-fit — fast, labeled directional). Scenarios are saved for side-by-side comparison.
3. **Data refresh + run history** — one-click background jobs (refresh deals, re-ingest budget workbook, rebuild MMM, run GTM audit); dataset freshness + row counts; live job logs.
4. **GTM audit browser** — a generic renderer mapping each skill's result JSON (trend, categorical, RF, kmeans, cohort, …) to a table + chart.

### Local run and Railway deploy

- **Local:** `python3 -m uvicorn webapp.main:app --reload`; deps in `requirements-web.txt`; `.env` supplies `HUBSPOT_TOKEN` (+ optional `APP_PASSWORD`); SQLite + parquet under `./data`.
- **Railway (later):** `Dockerfile` + `railway.toml` are included — single web service on a **Python 3.11** image (which also unlocks the `pymc` MMM backend), `uvicorn … --host 0.0.0.0 --port $PORT`, with a **persistent Volume mounted at `/data`** (SQLite + parquet need durable disk or a redeploy wipes them) and `PIPELINE_DATA_DIR=/data`.

---

## 19. Marketing Influence Report (repeatable cohort report)

A productized version of an analysis we used to run by hand every time: **how much created pipeline and
how many created deals carried an organic/direct/blog marketing signal**, split influenced-vs-cold, over
time. It exists because the ad-hoc version was non-reproducible, token-expensive, and un-consumable from
the web app.

**Design — one self-contained skill, not a manifest chain.** Unlike the contact-level analysts (which
chain `hs_pull_* → py_feature_engineering → py_*`), the influence report is a **single skill**
(`skills/reports/influence_report.py`) that runs the whole chain internally via `HubSpotClient`. The
reason: the graph traversal (deal IDs → associations → contact/company IDs → batch-read) can't thread
cleanly through separate subprocess steps, and batch-reading by arbitrary ID list isn't an existing
skill. One skill = one agent tool-call = one web-app job. That is the token-efficiency and determinism win.

**The graph** (per deal created in `[start, end]` on `pipeline`):
```
deal
├── directly associated contacts        → contact signals
└── associated companies
    ├── company object signals           → company signals
    └── company's associated contacts    → contact signals   (capped at N=25/company)
```

**Locked signal set** (in `skills/common/marketing_signals.py`, the single source of truth): contact
`hs_analytics_source`/`hs_latest_source` ∈ {ORGANIC_SEARCH, DIRECT_TRAFFIC}; contact first/last URL or
referrer contains "blog"; company `hs_analytics_source` ∈ {ORGANIC_SEARCH, DIRECT_TRAFFIC}; company
LinkedIn organic impressions/engagements (90d) > 0 (Fibbler account id **discovered dynamically**, never
hardcoded). A deal is *influenced* if any node carries any signal.

**Modules:**
- `skills/common/marketing_signals.py` — locked definitions + `discover_linkedin_organic_props`, `contact_signal_flags`, `company_signal_flags`, `target_definition_text`.
- `skills/common/deal_graph.py` — `build_deal_graph(...)`: the pull/association/batch-read chain, caching raw parquets to `data/raw/`.
- `skills/reports/influence_report.py` — orchestrates graph → signals → deal rollup → period cohorts; writes detail parquet + manifest + results JSON; emits the envelope.

**Outputs** per run: per-period influenced-vs-cold deal count + pipeline $, a sub-signal breakdown, totals
with %, a Rule-6 target-definition string, and a Rule-3 sanity line. Deterministic: identical params →
byte-identical results (no randomness).

**Consumption:**
- **Chat** — the `marketing-influence-analyst` agent (on-demand, not in the default audit) makes the ONE
  skill call and writes the briefing; it never re-derives the logic inline.
- **Web app** — a fifth surface, `/influence`: date-range + granularity + company-contact-cap controls,
  enqueues the skill as a background job, polls via HTMX, and renders headline cards, a Plotly
  influenced-vs-cold stacked bar + influence-rate line, a per-period cohort table, and the sub-signal
  breakdown. Runs are saved (via the standard `result_artifact` table + on-disk JSON) and revisitable,
  and also appear in the GTM audit browser through a dedicated renderer.

**Rule compliance:** organic/direct are OFFLINE-exclusive so the cohort is clean under Rule 5; the report
states its target definition (Rule 6) and sanity-checks influenced ≤ total (Rule 3). Caveat every run:
blog activity is undercounted because HubSpot stores only first/last URL per contact.

---

## TL;DR

A user asks a GTM question in Claude Code. An orchestrator agent decomposes it, dispatches one or more specialist agents (or calls skills directly), each of which invokes Python skills via Bash. HubSpot skills pull live data; statistical skills auto-resolve features from a column manifest written by `py_feature_engineering`. Every skill returns a compact JSON envelope; full results live on disk in `data/`. Analytical rules (intersection-based opportunity targets, marketing-cohort definition, sanity gates) are codified in `docs/analysis_rules.md` and enforced via agent system prompts. The orchestrator synthesizes across specialists and writes the user-facing briefing. Past mistakes live in persistent memory files that auto-load into every session. The math is reproducible; the reasoning is auditable; the data on disk is inspectable. Three extensions sit on top of this same foundation: a top-down **Marketing Mix Model** (§17) that turns external spend into channel-level pipeline attribution, a deterministic **web application** (§18) that runs the skills and visualizes their results outside the chat window, and a repeatable **Marketing Influence Report** (§19) — one self-contained skill that measures organic/direct/blog influence on created pipeline over time, consumable from both chat and a dedicated web-app surface. All reuse the skill contract unchanged.
