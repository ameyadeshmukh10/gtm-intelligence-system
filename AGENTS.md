# GTM Intelligence System — Agents Guide

This file is auto-loaded into Claude Code and describes the project layout, design contract, and invocation patterns for everything in this repo. Read this first.

## ⚠️ Analytical rules — MUST follow before any contact-level analysis

**Read [docs/analysis_rules.md](docs/analysis_rules.md) in full before running any analytical skill that computes a rate, lift, or trend on contacts.** The rules are compressed below; the full file is authoritative.

1. **Opportunity target definition.** A contact counts as an opportunity ONLY if `lifecyclestage ∈ {opportunity, salesqualifiedlead, customer}` **AND** the contact is associated with a deal in the windowed deal set. **Set intersection, not union.** Never include custom numeric lifecyclestage IDs (e.g. `2265264367`) — these are typically marketing-workflow stages like "Sales Accepted Lead" and inflate rates by 20–30×.
2. **Trend analysis must segment by `recent_conversion_event_name` bucket.** Global monthly trends hide composition shifts. Every contact-level trend output must produce a month × program-bucket grid, not just a global time series. Report per-bucket trend direction alongside aggregate.
3. **Sanity-check every rate against population ground truth.** Before reporting any rate, multiply (`rate × n`) back out to an absolute count and compare against known deal count in window. If implied positives > 2 × deal count, the target is wrong — stop and re-examine.
4. **Custom lifecyclestage IDs must be resolved before use.** Numeric IDs get labels via `hs_pull_custom_properties`. Never assume a numeric stage means "opportunity" based on frequency.
5. **Report the target definition alongside every rate.** One-sentence description at the top of any analytical output, including positive count, total n, and baseline rate.

Past incident (2026-04-21): using `lifecyclestage ∈ {opportunity, SQL, customer, 2265264367} OR in_deal_assoc` produced a 46.67% "opportunity rate" against 10,951 contacts in a window containing only 502 deals. Correct rate under the intersection rule: 1.72%. The entire contact-level audit had to be redone. **This must not repeat.**

## What this is

A fully-agentic port of the EverWorker GTM Intelligence System (see `EverWorker_GTM_Intelligence_System_v2-1.docx`). Six agents sit on top of a reusable skill library (HubSpot API connectors + Python statistical skills) and deliver pipeline intelligence on demand. Design principle: **the LLM reasons, the Python skills compute, neither does the other's job.**

## Repo layout

```
.
├── AGENTS.md                    ← you are here
├── README.md                    ← quickstart
├── .env                         ← HUBSPOT_TOKEN + client secret (gitignored)
├── requirements.txt
├── .claude/agents/              ← agent brains (Claude Code subagents)
│   ├── gtm-orchestrator.md
│   ├── pipeline-progression-analyst.md
│   ├── program-attribution-analyst.md
│   ├── signal-combination-analyst.md
│   ├── trend-intelligence-analyst.md
│   └── icp-synthesis-analyst.md
├── skills/
│   ├── common/                  ← I/O contract, manifest, cache helpers
│   ├── hubspot/                 ← 8 HubSpot API connector skills
│   └── python/                  ← 12 Python statistical skills
├── data/                        ← artifact cache (gitignored)
│   ├── raw/                     ← parquet from hs_pull_*
│   ├── features/                ← parquet + manifest from py_feature_engineering
│   └── results/                 ← JSON from every analytical skill
└── docs/
    ├── analysis_rules.md       ← MUST-read authoritative target + cohort rules
    ├── skills.md
    ├── workflows.md
    └── interpretation.md
```

## How to use

Every user-facing request goes to the **gtm-orchestrator** agent. It decomposes the prompt and either:
- dispatches one or more specialist analysts, or
- calls skills directly for simple data pulls or single statistical tests.

Example prompts:

| Prompt | Likely routing |
|---|---|
| "What predicts whether a Stage 0 deal advances to Stage 1?" | `pipeline-progression-analyst` |
| "Which marketing events produce the most pipeline?" | `program-attribution-analyst` |
| "Do contacts who attend an event AND submit an inbound form convert at higher rates?" | `signal-combination-analyst` |
| "Is our pipeline conversion rate improving over the last 12 months?" | `trend-intelligence-analyst` |
| "Who is our best ICP based on what actually closes?" | `icp-synthesis-analyst` |
| "Run a full GTM audit and tell me what to change." | All five in sequence |
| "Pull every Stage 1 deal created since January" | Direct skill call (no specialist) |

## Skill contract (mandatory)

Every skill — HubSpot or Python — is a standalone Python module invoked via:

```bash
python -m skills.<group>.<skill_name> '<json params as single arg>'
# OR
echo '<json params>' | python -m skills.<group>.<skill_name>
```

Every skill prints a single-line JSON envelope to stdout:

```json
{
  "results":  <dict or list>,
  "summary":  "2-3 sentence plain-English summary",
  "metadata": {
    "n_records": <int>,
    "n_features": <int>,
    "warnings":  [<str>, ...],
    "artifacts": {"parquet": "data/raw/...", "json": "data/results/..."}
  }
}
```

Full results are cached on disk — agents read the stdout summary, and use the `Read` tool on the artifact paths only when they need granular numbers. This keeps the LLM context window tight.

### Error envelope

```json
{"results": null, "summary": "ERROR: <message>",
 "metadata": {"n_records": 0, "n_features": 0,
              "warnings": [], "error": "<message>"}}
```

## The manifest-driven architecture

`py_feature_engineering` is the foundation — **every other Python skill depends on it running first**. It:

1. dedupes raw HubSpot pulls,
2. parses multi-value fields (semicolon-delimited),
3. ordinal-encodes seniority/revenue,
4. creates `has_any_*` rollups and per-value binary flags,
5. computes date-derived features (`days_since_*`, `*_month_idx`),
6. emits a **column manifest** classifying every column into: `id` / `target` / `numeric` / `ordinal` / `categorical` / `binary` / `rollup` / `date` / `date_derived` / `excluded`.

Downstream skills (`py_mann_whitney`, `py_categorical_conversion`, etc.) read the manifest and auto-resolve which features to analyze. **The LLM never has to enumerate column names in skill calls** — it just passes `run_id` and an optional `target` override.

## Contamination columns (always excluded)

`skills/common/manifest.py::CONTAMINATION_COLUMNS` blacklists columns that are downstream consequences of the outcome and would leak the target:

```
meeting_booked, hs_meeting_booked, demo_completed, sql_created,
mql_created, opportunity_created, closed_date, closedate, hs_closedate
```

If Random Forest AUC > 0.95, the skill automatically flags likely leakage and lists the top features to investigate.

## Data quality protocol (enforced by every agent)

Before reporting findings, all analysts must:
1. **Detect duplicates.** `py_feature_engineering` dedupes on `id` and emits a warning with the drop count.
2. **Flag date sequence errors.** `py_stage_conversion` excludes deals where destination stage date precedes source and reports the count dropped.
3. **Identify >50% null fields.** `py_feature_engineering` warns on these.
4. **Normalize multi-value fields.** FE handles `;` `,` `|` delimiters and strips test/draft entries.
5. **Report final n after exclusions** alongside raw pull counts.

## Statistical interpretation rules (shared by all agents)

See `docs/interpretation.md` for the full cheat sheet. Highlights:

- **Mann-Whitney** p<0.05 with meaningful median gap → report. p>0.1 → don't.
- **Categorical** chi² p<0.05 and n≥15 → reliable. n<15 → directional only.
- **Random Forest** AUC>0.95 → leakage suspect. AUC 0.7–0.85 → real signal.
- **SYNERGY** delta>+10pp → routing-rule trigger.
- **SUPPRESSION** delta<−5pp → investigate mass-program dilution.
- **Cluster gap** >3× best-vs-worst → real archetype separation.
- **Spearman** |r|>0.4 and p<0.05 → meaningful trend.
- **Inverted medians** on volume metrics → mass programs reaching unqualified audiences.

Never claim causation. Always state n alongside rates. Distinguish statistical from practical significance.

## Credentials

HubSpot private-app token lives in `.env`:

```
HUBSPOT_TOKEN=pat-eu1-...
HUBSPOT_BASE_URL=https://api.hubapi.com
```

All HubSpot skills load this via `python-dotenv`. The token is EU-region but uses the same base URL. Rotate by editing `.env` — no code change required.

## Adding a new skill

1. Create `skills/<group>/<skill_name>.py`.
2. Use `skills.common.io_contract.read_params()` to consume JSON args.
3. Use `skills.common.io_contract.emit()` / `emit_error()` to return the envelope.
4. Write artifacts to `data/raw/`, `data/features/`, or `data/results/` — use the helpers in `io_contract`.
5. Update `docs/skills.md`.
6. Add the invocation line to any agent `.md` that should use it.

## Adding a new agent

1. Create `.claude/agents/<name>.md` with frontmatter `name`, `description`, `tools`, `model`.
2. Write the role/instructions/output-format/final-instructions body using the five existing specialists as a template.
3. Add the agent to the orchestrator's dispatch list in `.claude/agents/gtm-orchestrator.md`.
4. Update the prompt→routing table above.
