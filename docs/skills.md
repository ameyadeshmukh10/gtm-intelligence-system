# Skill Reference

Every skill is invoked as `python -m skills.<group>.<skill_name> '<json params>'` and returns a `{results, summary, metadata}` envelope. Full results are cached to `data/` — use the artifact paths in metadata to read granular output.

## HubSpot API skills (skills/hubspot/)

### hs_pull_custom_properties
Introspect available properties on an object type. **Call this first** whenever field names are uncertain.

```json
{"object_type": "deals", "filter_name": "stage", "show_options": false}
```

### hs_pull_deals
Retrieve Deal records with stage dates, pipeline, territory, owner.

```json
{
  "properties": ["dealname","pipeline","dealstage","amount","createdate",
                 "hs_date_entered_qualifiedtobuy"],
  "filters": [{"property":"pipeline","operator":"EQ","value":"Sales Pipeline"},
              {"property":"createdate","operator":"GTE","value":"2025-01-01"}],
  "associations": ["contacts","companies"],
  "limit": "all"
}
```

### hs_pull_contacts
Contact firmographics, program enrollment, engagement.

```json
{
  "properties": ["email","seniority","num_associated_deals",
                 "events_attended","hs_analytics_source"],
  "filters": [],
  "limit": 10000
}
```

### hs_pull_companies
Firmographics by industry, revenue, size.

### hs_pull_associations
Resolve object→object associations.

```json
{"from_type":"deals","to_type":"contacts","ids_from":"data/raw/deals_<hash>.parquet"}
```

### hs_pull_engagements
Calls, emails, meetings, notes, tasks. Filter by `associated_type`+`associated_ids` or `since`/`until`.

### hs_pull_marketing_events
Events + per-contact attendance (registered/attended/cancelled/noshow).

### hs_pull_email_stats
Per-contact email rollup (`contact_rollup` mode) or event stream (`event_stream` mode with `since`/`until`).

---

## Python statistical skills (skills/python/)

Every Python skill requires that `py_feature_engineering` has already produced a feature matrix + manifest for the given `run_id`.

### py_feature_engineering (foundation — run first)
Dedupe, parse, encode, impute; emit feature matrix + column manifest.

```json
{
  "input": "data/raw/contacts_<hash>.parquet",
  "run_id": "audit_2026q2",
  "target": "has_deal",
  "target_rule": {"expr": "num_associated_deals >= 1"},
  "multi_value_fields": ["events_attended","webinars_attended"],
  "exclude_values": {"events_attended": ["test_event","old_brand"]}
}
```

Outputs: `data/features/<run_id>.parquet` + `data/features/<run_id>_manifest.json`.

### py_mann_whitney
Batch Mann-Whitney U across all numeric features. Flags inverted medians.

```json
{"run_id":"audit_2026q2","target":"has_deal","p_cut":0.05}
```

### py_categorical_conversion
Per-category conversion rate + chi² + lift vs baseline.

```json
{"run_id":"audit_2026q2","columns":["territory","industry_group"],"min_n":5}
```

### py_random_forest
Balanced CV model; AUC/F1/importance. Flags leakage when AUC>0.95.

```json
{"run_id":"audit_2026q2","n_estimators":300,"drop_zero_variance":true}
```

### py_spearman
Single-pair or batch correlation.

```json
{"run_id":"audit_2026q2","reference":"days_to_next_stage"}
```

### py_stage_conversion
Pipeline stage-to-stage conversion matrix with velocity.

```json
{"run_id":"audit_2026q2","auto_stage_matrix":true}
```

### py_combination_analysis
Pairwise + triple binary-combo conversion rates.

```json
{"run_id":"audit_2026q2","min_n":5,"top_pairs":25,"top_triples":15}
```

### py_interaction_effects
2x2 interaction tables with SYNERGY/SUPPRESSION flags.

```json
{"run_id":"audit_2026q2",
 "pairs":[["has_any_events_attended","has_inbound_request"],
          ["has_any_pdf_downloads","has_any_academy_registrations"]]}
```

### py_kmeans_cluster
KMeans k=3,4,5 with silhouette selection and cluster profiling.

```json
{"run_id":"audit_2026q2","k_values":[3,4,5]}
```

### py_logistic_regression
Regularized L2 logistic regression; top positive/negative coefficients.

```json
{"run_id":"audit_2026q2","C":0.1,"include_interactions":true}
```

### py_trend_analysis
Monthly conversion rate + Spearman vs time index.

```json
{"run_id":"audit_2026q2","date_column":"createdate"}
```

### py_cohort_analysis
Early/mid/late cohort comparison (auto-terciles or explicit boundaries).

```json
{"run_id":"audit_2026q2","boundaries":["2025-06-01","2025-11-01"]}
```

---

## Marketing Mix Model skills (top-down, spend → pipeline)

The MMM path is independent of `py_feature_engineering` — it builds a *period-level*
time-series matrix, not a contact-level one. It requires **external marketing spend**
(HubSpot has none). Pipeline: `load_spend` → `py_mmm_features` → `py_marketing_mix_model`.
Driven by the **marketing-mix-analyst** agent.

### load_spend (skills/common/)
Ingest an external spend export (CSV/parquet, long or wide) and normalize raw channels into
4–6 modeling groups. Flags sparse channels and short series.

```json
{"input":"data/templates/spend_example.csv","granularity":"month","layout":"long",
 "channel_map":{"google_ads":"paid_search","linkedin_ads":"paid_social"}}
```

Outputs long parquet `(period, channel_group, spend[, impressions, clicks])`.

### py_mmm_features
Join deals-created-per-period (outcome) + spend-per-channel-per-period (media) + controls
(trend, seasonality, sales capacity) into one time-indexed matrix. Emits an MMM manifest and
warns when `params > periods` (under-identified — merge groups or aggregate weekly).

```json
{"run_id":"mmm_2026","deals_input":"data/raw/deals_<hash>.parquet",
 "spend_input":"data/raw/spend_<hash>.parquet","granularity":"month",
 "outcome":"deals_created"}
```

Outputs `data/features/<run_id>.parquet` + `data/features/<run_id>_mmm_manifest.json`.

### py_marketing_mix_model
Bayesian MMM: per-channel geometric **adstock** (carryover) + Hill **saturation**
(diminishing returns), additive on the response scale, NegBin outcome. Returns per-channel
contribution, marginal ROI, response curves, and baseline/incremental split — each with 90%
credible intervals. Backends: `laplace` (default, pure numpy/scipy — MAP + Gaussian posterior),
`metropolis` (numpy multi-chain, reports R-hat), `pymc` (NUTS, needs Python ≥3.10).

```json
{"run_id":"mmm_2026","backend":"laplace","draws":1500,"warmup":1500,"chains":4}
```

Outputs `data/results/<run_id>_marketing_mix_model.json`.

---

## Marketing Influence Report skill (self-contained, one call)

`skills/reports/influence_report.py` is a **single deterministic skill** that runs the entire
pull → enrich → signal → cohort chain for the organic/direct/blog influence report. It does NOT depend on
`py_feature_engineering`; it uses `HubSpotClient` directly and emits one envelope. Driven by the
**marketing-influence-analyst** agent and the web app `/influence` surface.

### influence_report (skills/reports/)
Pull deals in a window → resolve associated contacts + companies + capped company-contacts → detect the
locked organic/direct/blog signal set → bucket deals by `createdate` into week/month/quarter cohorts →
report influenced-vs-cold deal count + pipeline $ per period + a sub-signal breakdown.

```json
{"start":"2026-01-01","end":"2026-03-31","granularity":"quarter",
 "pipeline":"default","cap_company_contacts":25}
```

Params: `start`, `end` (ISO dates, inclusive), `granularity` ∈ {week, month, quarter},
`pipeline` (default "default"), `cap_company_contacts` (default 25; 0 disables the company-contact net),
optional `run_id` (defaults to `influence_<granularity>_<start>_<end>`).

Outputs: `data/features/<run_id>.parquet` (one row per deal + signal flags), `data/features/<run_id>_manifest.json`,
`data/results/<run_id>_influence_report.json`. Deterministic — identical params yield byte-identical results.

### Supporting modules (not standalone skills)
- **`skills/common/marketing_signals.py`** — the LOCKED signal definitions (the single source of truth):
  `discover_linkedin_organic_props`, `contact_signal_flags`, `company_signal_flags`, `target_definition_text`.
- **`skills/common/deal_graph.py`** — `build_deal_graph(client, start, end, pipeline, cap_company_contacts)`
  resolves deals→contacts→companies→capped company-contacts and batch-reads them; caches raw pulls to `data/raw/`.

See `docs/analysis_rules.md` (Appendix) for the authoritative signal definition.

---

## Filter operator reference (hs_pull_*)

| Input | HubSpot op |
|---|---|
| `EQ`, `=`, `==` | `EQ` |
| `NEQ`, `!=` | `NEQ` |
| `GT`, `>` | `GT` |
| `GTE`, `>=` | `GTE` |
| `LT`, `<` | `LT` |
| `LTE`, `<=` | `LTE` |
| `BETWEEN` | `BETWEEN` (requires `value` + `highValue`) |
| `IN`, `NOT_IN` | uses `values` list |
| `HAS_PROPERTY`, `NOT_HAS_PROPERTY` | no value |
| `CONTAINS_TOKEN` | `CONTAINS_TOKEN` |

The HubSpot search API caps at 10,000 results per query. For larger pulls without filters, skills use `/crm/v3/objects/{type}` (unlimited paginated GET).
