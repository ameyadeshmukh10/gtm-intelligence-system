# GTM Intelligence System

**An agentic GTM analytics engine built on live HubSpot data.** Ask a revenue question in plain English — *"what predicts whether a Stage 0 deal advances?"*, *"which marketing programs should we cut?"*, *"how should we split next quarter's budget?"* — and a team of eight AI agents decomposes it, pulls live CRM data, runs real statistics, and returns a briefing a VP of Sales or CMO can act on. Not a dashboard. Not a stats dump. An interpreted, data-cited analysis.

![Python](https://img.shields.io/badge/Python-3.9%2B-blue) ![Agents](https://img.shields.io/badge/AI%20agents-8-8A2BE2) ![Skills](https://img.shields.io/badge/deterministic%20skills-25-success) ![HubSpot](https://img.shields.io/badge/data-HubSpot%20CRM%20API-ff7a59) ![MMM](https://img.shields.io/badge/MMM-Bayesian-orange) ![Web](https://img.shields.io/badge/web%20app-FastAPI%20%2B%20HTMX%20%2B%20Plotly-009688)

Built end-to-end as a single system: multi-agent orchestration on Claude Code, a 25-skill deterministic analytics library, a Bayesian Marketing Mix Model, an incident-hardened analytical rules engine, and a deployable web application — all held together by one design principle:

> **The LLM reasons. Python computes. Neither does the other's job.**

---

## What you can ask it

| Prompt | What happens |
|---|---|
| "Run a full GTM audit and tell me what to change" | Five specialist agents run in sequence over live deal + contact data; the orchestrator synthesizes cross-agent findings into ranked recommendations |
| "What predicts whether a Stage 0 deal advances to Stage 1?" | Stage-transition matrix, Mann-Whitney feature screening, Random Forest importance ranking, owner-level conversion breakdown |
| "Which marketing events produce the most pipeline?" | Per-program conversion + lift vs baseline across every event, webinar, nurture, and content asset, with negative-lift programs flagged |
| "Are our nurtures suppressing our best signals?" | 2×2 interaction-effect analysis with SYNERGY/SUPPRESSION detection across signal pairs |
| "Is our conversion rate actually improving?" | Monthly trend segmented by program bucket — separating real quality shifts from channel-mix composition shifts |
| "Who is our ICP based on what actually closes?" | Cross-stage ICP synthesis: which segments hold up at *every* pipeline gate, and which are false positives that open well but stall |
| "How much pipeline did organic/direct/blog influence this quarter?" | Deterministic deal-graph traversal (deals → contacts → companies) scoring every deal against a locked marketing-signal set |
| "How should we split next quarter's budget across channels?" | Bayesian Marketing Mix Model: per-channel contribution with 90% credible intervals, marginal ROI, and response curves |

---

## Capabilities

### 1. Multi-agent orchestration

Eight Claude Code subagents (`.claude/agents/*.md`), each a version-controlled markdown "brain" with its own tools, playbook, output format, and guardrails:

| Agent | Domain |
|---|---|
| `gtm-orchestrator` | Single entry point — decomposes prompts, routes to specialists, sanity-checks their outputs, synthesizes cross-agent findings |
| `pipeline-progression-analyst` | Stage-to-stage conversion, stall points, velocity, rep-execution vs ICP-quality attribution |
| `program-attribution-analyst` | Which events, webinars, nurtures, and content assets are associated with deal-producing contacts — and which generate volume without conversion |
| `signal-combination-analyst` | Signal pair/triple combos, SYNERGY/SUPPRESSION interaction effects, behavioral archetype clustering, lead-scoring rules |
| `trend-intelligence-analyst` | Monthly conversion trends, channel-mix shift decomposition, cohort comparisons, event-window impact |
| `icp-synthesis-analyst` | Composite ICP scored at every stage transition — tiered targeting (Tier 1 / Tier 2 / Deprioritize) with data citations |
| `marketing-influence-analyst` | Repeatable organic/direct/blog influence cohort report over created pipeline |
| `marketing-mix-analyst` | Top-down Bayesian spend→pipeline model for budget allocation, marginal ROI, and scenario forecasting |

The orchestrator runs single-domain questions through one specialist, fans multi-domain questions out in parallel, chains a full audit through five specialists in canonical order with condensed context passing — and **rejects** any specialist result whose baseline rate fails a sanity check rather than forwarding inflated numbers.

### 2. Live HubSpot data engine

Eight API connector skills (`skills/hubspot/`) over a shared client that handles auth, pagination (list + search endpoints), 429/5xx retry with backoff, batch reads (100-object chunks), and v4 association resolution (1,000-id chunks):

- **Deals, contacts, companies** — filtered pulls with operator normalization (`>=` → `GTE`, `BETWEEN`, `IN`, `HAS_PROPERTY`)
- **Associations** — deal↔contact↔company graph edges at scale
- **Engagements** — calls, emails, meetings, notes, tasks unified into one stream
- **Marketing events** — including per-state attendance (registered / attended / cancelled / no-show)
- **Email stats** — contact-rollup and event-stream modes
- **Schema introspection** — resolves per-instance custom properties and stage-date naming before any analysis, making every skill portable across HubSpot instances

All read-only. Every pull is cached to parquet, keyed by a hash of the query, so identical queries never hit the API twice.

### 3. Statistical engine — 12 manifest-driven analysis skills

A feature-engineering foundation plus 11 analytical skills (`skills/python/`), each a deterministic, independently runnable, seeded Python module:

| Method | What it's used for |
|---|---|
| Mann-Whitney U (batch) | Screen every numeric feature against conversion; flags inverted medians that expose mass programs reaching unqualified audiences |
| Chi-square + lift analysis | Per-segment conversion vs baseline for every categorical/binary feature, with small-n (`n<15`) results demoted to directional |
| Random Forest (balanced, 5-fold stratified CV) | Feature importance ranking with automatic leakage detection — AUC > 0.95 triggers a leakage flag, not a celebration |
| Spearman rank correlation | Trend and monotonic-relationship testing, single-pair or batch |
| Stage-conversion matrix | Auto-detected per-transition rates + median days-in-stage, with bad date sequences dropped and counted |
| Combination analysis | Pairwise + triple signal combos ranked by lift |
| 2×2 interaction effects | Additive-expected vs observed, with SYNERGY (Δ > +10pp) and SUPPRESSION (Δ < −5pp) flags |
| KMeans clustering | k selected by silhouette score; clusters profiled into named behavioral archetypes with deal-rate gaps |
| L2 logistic regression | Coefficient-level signal direction, with optional interaction terms |
| Trend analysis | Monthly conversion + Spearman vs month index, recency-bias-aware |
| Cohort analysis | Tercile cohort splits with chi-square across cohorts and per-cohort feature profiles |

The trick that makes it all composable: `py_feature_engineering` classifies every column into a **manifest** (numeric / categorical / binary / rollup / date-derived / excluded), and every downstream skill auto-resolves its inputs from it. Agents pass a `run_id`, never column lists — the LLM stays out of the bookkeeping loop entirely.

### 4. Bayesian Marketing Mix Model

A top-down spend→pipeline model (`py_mmm_features` + `py_marketing_mix_model` + two spend-ingestion skills) that complements the bottom-up attribution agents:

- **Per-channel geometric adstock** (carryover) + **Hill saturation** (diminishing returns)
- **Negative-Binomial likelihood**, additive on the response scale — baseline + channel contributions sum to the fitted total *exactly*
- **Three Bayesian backends:** Laplace approximation (NumPy/SciPy, runs anywhere), Metropolis MCMC with Gelman-Rubin R̂ convergence diagnostics, and PyMC NUTS
- Outputs per-channel contribution with **90% credible intervals**, marginal ROI, response curves, and a baseline-vs-incremental split
- Ingestion pipeline parses an Apple `.numbers` budget workbook into tidy spend data and groups channels to keep the model identifiable on thin monthly data — and **warns explicitly when it's under-identified** instead of printing confident nonsense

MMM answers *budget allocation*; program attribution answers *program cuts*. The system knows the difference and enforces it.

### 5. Marketing Influence Report — a productized analysis

An ad-hoc analysis turned into one deterministic, repeatable skill (`skills/reports/influence_report.py`): for every deal created in a window, it traverses the full deal graph (deal → associated contacts → associated companies → company contacts, capped), scores every node against a **locked, versioned marketing-signal set** (organic search, direct traffic, blog touches, LinkedIn organic engagement with dynamically discovered properties), and buckets deals into weekly/monthly/quarterly influenced-vs-cold cohorts with pipeline dollars. Identical params → byte-identical results. One skill call = one agent tool call = one web-app job.

### 6. Analytical rules engine — statistical integrity as code

The part most analytics projects skip. `docs/analysis_rules.md` codifies hard-won target-definition and cohort rules, each documented with the real incident that motivated it:

- **Intersection-based opportunity targets** — a union-based target once produced a 46.67% "opportunity rate" that was actually 1.72%. The rule, and the incident, are now written down and enforced in every agent's system prompt.
- **Sanity gates on every rate** — implied positives are multiplied back out against known deal counts before any rate is reported.
- **Trend decomposition discipline** — global trends must be segmented by program bucket, because a "conversion collapse" once turned out to be 70% channel-mix shift.
- **Marketing-cohort hygiene** — OFFLINE-source contacts (rep prospecting, list imports) are excluded from marketing attribution.
- **Contamination-column blacklisting** — outcome-leaking columns (`closedate`, `meeting_booked`, …) are auto-excluded from every model.
- **Correlation-not-causation discipline** — causal language is forbidden without a natural experiment; every output states its target definition, n, and baseline.

Past failures also persist as memory files that auto-load into every future session — the system literally cannot forget its own incidents.

### 7. Web application — the same engine, no chat required

A deterministic FastAPI + HTMX + Plotly app (`webapp/`) that runs every skill **unchanged** as a subprocess and visualizes the results. Server-rendered, no JS build step, SQLite storage, background job queue with live log streaming — one process, Railway-deployable with the included `Dockerfile` + `railway.toml`.

| Surface | What it does |
|---|---|
| Overview `/` | System status, dataset freshness, recent jobs |
| Marketing Mix `/mmm` | Contribution bars with 90% CI error bars, response curves, baseline-vs-incremental, spend-vs-deals timeline — with under-identification and calibration caveats surfaced as UI annotations, not hidden |
| Budget What-If `/mmm/whatif` | Enter a per-channel spend plan → projected incremental deals from fitted response curves; scenarios saved for side-by-side comparison |
| Influence `/influence` | Run and browse influence reports: headline cards, stacked influenced-vs-cold chart, per-period cohort table, sub-signal breakdown |
| Data & Runs `/data` | One-click background jobs (refresh HubSpot data, ingest budget workbook, rebuild MMM), run history, live job logs |
| GTM Audit `/audit` | Generic renderer for every analytical skill's result JSON — tables + charts for trend, conversion, RF, clusters, cohorts, stages |

### 8. Token-efficient agent/skill contract

Every one of the 25 skills speaks the same protocol: JSON params in, one compact JSON envelope out (`results` / `summary` / `metadata.warnings` / `metadata.artifacts`), full data cached to parquet/JSON on disk. Agents see **~500 bytes per skill call**, never 50 MB dataframes — and drill into disk artifacts only when a citation needs granular numbers. Three cache layers (raw pulls keyed by query hash, feature matrices keyed by `run_id`, results keyed by `run_id + skill`) make audits resumable after any crash and follow-up questions nearly free.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  User prompt (Claude Code chat)          Web app (FastAPI UI)   │
└─────────────────────────────────────────────────────────────────┘
                 │                                  │
                 ▼                                  │
┌────────────────────────────────────────┐          │
│  gtm-orchestrator                      │          │
│   · decomposes prompt → routing plan   │          │
│   · dispatches specialists             │          │
│   · sanity-checks + synthesizes        │          │
└────────────────────────────────────────┘          │
     │        │        │       │       │            │
     ▼        ▼        ▼       ▼       ▼            │
 pipeline  program  signal   trend    icp    (+ influence, mmm    │
 progress  attrib   combo    intel   synth      on demand)        │
     │        │        │       │       │            │
     └────────┴────────┴───┬───┴───────┴────────────┘
                           ▼
              ┌────────────────────────────┐
              │  skills/ — 25 deterministic │
              │  Python skills              │
              │   hubspot/  8 connectors    │
              │   python/  14 stat skills   │
              │   common/   ingestion + I/O │
              │   reports/  influence       │
              └────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │  data/ — parquet + JSON     │
              │   raw/      (API pulls)     │
              │   features/ (matrix+manifest│
              │   results/  (analyses)      │
              └────────────────────────────┘
```

The full deep-dive — every contract, the manifest design, caching, the rules engine, an end-to-end audit walkthrough, known limits, and the reasoning behind every design choice — lives in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Quickstart

```bash
# 1. install deps
pip install -r requirements.txt

# 2. set your HubSpot private-app token
cp .env.example .env   # add HUBSPOT_TOKEN

# 3. smoke test the HubSpot connection
python -m skills.hubspot.hs_pull_custom_properties '{"object_type":"deals"}'

# 4. in a Claude Code chat in this directory, just ask:
#    "Run a full GTM audit and tell me what to change"
#    "What predicts whether a Stage 0 deal advances to Stage 1?"
#    "Which marketing events produce the most pipeline?"
```

Every skill also runs standalone from a terminal — no agent required:

```bash
# HubSpot skill — pull deals
python -m skills.hubspot.hs_pull_deals '{"properties":["dealname","dealstage","amount"],"limit":"all"}'

# Feature engineering on a cached pull → feature matrix + column manifest
python -m skills.python.py_feature_engineering '{"input":"data/raw/deals_abc123.parquet","run_id":"audit_2026q1"}'

# Any analysis skill — just the run_id, features auto-resolved from the manifest
python -m skills.python.py_mann_whitney '{"run_id":"audit_2026q1"}'
```

And the web app:

```bash
pip install -r requirements-web.txt
uvicorn webapp.main:app --reload   # → http://localhost:8000
```

## Repo map

| Path | What it is |
|---|---|
| `AGENTS.md` | System contract auto-loaded into every Claude Code session |
| `ARCHITECTURE.md` | Full technical deep-dive (18 sections) |
| `.claude/agents/*.md` | The eight agent brains |
| `skills/hubspot/` | 8 HubSpot API connector skills + shared client |
| `skills/python/` | 14 statistical skills (12 manifest-driven + 2 MMM) |
| `skills/common/` | I/O contract, manifest, deal-graph builder, marketing-signal definitions, spend ingestion |
| `skills/reports/` | Self-contained report skills (influence report) |
| `webapp/` | FastAPI + HTMX + Plotly web application ([webapp/README.md](webapp/README.md)) |
| `docs/analysis_rules.md` | The authoritative target-definition and cohort rules |
| `docs/skills.md` · `docs/workflows.md` · `docs/interpretation.md` | Skill reference, routing patterns, statistical interpretation thresholds |
| `Dockerfile` · `railway.toml` | Railway deployment (Python 3.11, persistent volume) |

## Honest by design

The system is deliberately conservative about its own claims: small-n results are labeled directional, suspiciously high AUCs are flagged as leakage instead of reported as wins, under-identified MMM channels are called out, and every rate ships with its target definition and n. All HubSpot access is read-only, and every claim is traceable to a cached artifact you can open yourself. Known limits and technical debt are documented in [ARCHITECTURE.md §15](ARCHITECTURE.md#15-known-limits-and-technical-debt), not buried.

---

**Built by [Ameya Deshmukh](https://github.com/ameyadeshmukh10)** — a working example of GTM engineering: agentic AI systems, marketing science, and production software applied to a real revenue pipeline.
