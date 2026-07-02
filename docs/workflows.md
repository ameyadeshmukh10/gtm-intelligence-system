# Workflow Patterns

## Full GTM audit (5 specialists in sequence)

Triggered by prompts like "run a full audit" or "tell me what to change". The orchestrator dispatches in this order, passing accumulated context between steps:

```
Step 1: pipeline-progression-analyst
  - hs_pull_deals, hs_pull_contacts via associations, hs_pull_engagements
  - py_feature_engineering (run_id = "audit_<yyyymm>")
  - py_stage_conversion (auto_stage_matrix)
  - py_mann_whitney, py_categorical_conversion, py_random_forest, py_spearman
  Output: stage rates, top predictors, velocity signals

Step 2: program-attribution-analyst
  - Reuses run_id's contact data if possible; adds hs_pull_marketing_events
  - py_categorical_conversion on program binary columns, py_random_forest
  Output: Tier 1/2/3/Negative programs, nurture analysis, negative-lift flags

Step 3: signal-combination-analyst
  - Same run_id — reuses feature matrix
  - py_combination_analysis, py_interaction_effects, py_kmeans_cluster,
    py_logistic_regression (with interactions)
  Output: top pairs, SYNERGY/SUPPRESSION flags, named archetypes

Step 4: trend-intelligence-analyst
  - Same run_id. Adds py_trend_analysis, py_cohort_analysis, py_spearman
  Output: trend direction, drivers, cohort delta, event spikes, hypotheses

Step 5: icp-synthesis-analyst
  - Receives accumulated context from all 4 prior workers
  - py_stage_conversion per gate, py_categorical_conversion per gate,
    py_random_forest for early vs late targets
  Output: composite ICP, tiered breakdown, false positive segments

Orchestrator synthesis:
  - 3-5 highest-confidence findings across all 5 workers
  - One specific action per finding with supporting data
  - Open questions section
```

**Context passing** — the orchestrator does NOT pass raw dataframes between workers (too big for context). Instead it passes:
- The `run_id` (lets the next worker load the same feature matrix)
- A condensed summary of the prior worker's findings (5-10 bullet points)

## Single-worker dispatch

Quick reference for mapping user prompts to a specialist:

| Prompt pattern | Worker |
|---|---|
| "what predicts S0→S1 / stage conversion / why deals stall" | pipeline-progression-analyst |
| "which programs / events / webinars / nurtures work" | program-attribution-analyst |
| "which signal combinations / synergies / archetypes" | signal-combination-analyst |
| "is conversion rate improving / trend / cohort / event impact" | trend-intelligence-analyst |
| "who is our ICP / who to prioritize / target account strategy" | icp-synthesis-analyst |
| "how much pipeline/deals influenced by organic/direct/blog, by week/month/quarter" | marketing-influence-analyst |
| "how to split budget across channels / marginal ROI by channel" | marketing-mix-analyst |

## Parallel multi-worker dispatch

For requests that span independent domains (e.g. "which programs work AND is pipeline quality improving?"), the orchestrator can dispatch workers in parallel:

```
parallel:
  - program-attribution-analyst
  - trend-intelligence-analyst
then:
  - synthesize across both outputs
```

Parallel is safe because the workers don't share state within a request — each loads its own `run_id` or uses a shared one produced by `py_feature_engineering`. Running in parallel roughly halves wall time for independent analyses.

## Direct skill calls (no specialist)

Orchestrator bypasses specialists when the request is:
- A data pull: `"pull all Stage 1 deals created since Jan 1"` →
  ```bash
  python -m skills.hubspot.hs_pull_deals '{
    "filters":[{"property":"dealstage","operator":"EQ","value":"S1"},
               {"property":"createdate","operator":"GTE","value":"2026-01-01"}],
    "limit":"all"}'
  ```
- A single test: `"run Mann-Whitney on sessions vs conversion"` →
  ```bash
  # assume run_id "audit_2026q2" is already engineered
  python -m skills.python.py_mann_whitney '{"run_id":"audit_2026q2","features":["hs_analytics_num_visits"]}'
  ```
- Schema introspection: `"what properties do we have on deals"` →
  ```bash
  python -m skills.hubspot.hs_pull_custom_properties '{"object_type":"deals"}'
  ```

## Caching + reuse

- `data/raw/<object>_<hash>.parquet` — HubSpot pulls are keyed by a hash of the query. Re-running an identical query hits the same file.
- `data/features/<run_id>.parquet` + `_manifest.json` — feature engineering output. Every downstream Python skill reads these.
- `data/results/<run_id>_<skill>.json` — analytical outputs. The orchestrator can read any of these to answer follow-up questions without re-running.

When the user asks a follow-up referencing prior work ("break down the top tier programs by territory"), check `data/results/` first before re-running anything.

## When to re-pull vs reuse

| Signal | Action |
|---|---|
| Same session, same scope | Reuse run_id |
| New scope but same raw data (different target) | New run_id, reuse raw parquets |
| Different date range or pipeline | New hs_pull_*, new run_id |
| More than 4 hours since pull | Consider re-pull (data may have shifted) |
