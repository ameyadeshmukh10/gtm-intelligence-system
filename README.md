# GTM Intelligence System — Claude Code edition

A fully-agentic port of the EverWorker GTM Intelligence System spec, adapted to run inside Claude Code. A top-level orchestrator routes any GTM analytical question to one of five specialist analyst subagents, each composed of HubSpot API connector skills (live data pulls) and Python statistical skills (deterministic computation).

## Quickstart

```bash
# 1. install deps
pip install -r requirements.txt

# 2. confirm .env has HUBSPOT_TOKEN set (already scaffolded)
cat .env

# 3. smoke test the HubSpot connection
python -m skills.hubspot.hs_pull_custom_properties '{"object_type":"deals"}'

# 4. in a Claude Code chat, address the orchestrator:
#    "Run a full GTM audit and tell me what to change"
#    "What predicts whether a Stage 0 deal advances to Stage 1?"
#    "Which marketing events produce the most pipeline?"
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  User prompt in Claude Code chat                                │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  gtm-orchestrator (.claude/agents/gtm-orchestrator.md)          │
│    · decomposes prompt → routing plan                            │
│    · dispatches specialists or calls skills directly             │
└─────────────────────────────────────────────────────────────────┘
                │             │            │           │           │
                ▼             ▼            ▼           ▼           ▼
       ┌─────────────┐ ┌────────────┐ ┌──────────┐ ┌────────┐ ┌────────┐
       │ pipeline-   │ │ program-   │ │ signal-  │ │ trend- │ │ icp-   │
       │ progression │ │ attribution│ │ combo    │ │ intel  │ │ synth  │
       └─────────────┘ └────────────┘ └──────────┘ └────────┘ └────────┘
                │             │            │           │           │
                └─────────────┴────────────┴───────────┴───────────┘
                                     │
                                     ▼
                        ┌────────────────────────────┐
                        │  skills/ — python scripts   │
                        │   hubspot/ (8 API skills)   │
                        │   python/  (12 stat skills) │
                        │   common/  (I/O, manifest)  │
                        └────────────────────────────┘
                                     │
                                     ▼
                        ┌────────────────────────────┐
                        │  data/ — parquet + JSON     │
                        │   raw/    (api pulls)       │
                        │   features/ (post-FE)       │
                        │   results/  (analyses)      │
                        └────────────────────────────┘
```

## How skills are invoked

Every skill is a standalone Python script that:
- reads params as a JSON positional arg or via stdin
- writes the full result to a parquet/JSON file in `data/`
- prints a compact JSON summary to stdout for the agent to read

This keeps agent context small — the LLM sees summary stats, not 50MB dataframes.

```bash
# HubSpot skill — pull deals
python -m skills.hubspot.hs_pull_deals '{"properties":["dealname","dealstage","amount"],"limit":"all"}'

# Python skill — run feature engineering on a cached pull
python -m skills.python.py_feature_engineering '{"input":"data/raw/deals_abc123.parquet","run_id":"audit_2026q1"}'

# Python skill — Mann-Whitney against the manifest
python -m skills.python.py_mann_whitney '{"run_id":"audit_2026q1","target":"converted"}'
```

## Files

- `AGENTS.md` — top-level system description (auto-loaded by Claude Code)
- `.claude/agents/*.md` — the six agent brain definitions
- `skills/hubspot/*.py` — 8 HubSpot API connector skills
- `skills/python/*.py` — 12 Python statistical skills
- `skills/common/*.py` — shared I/O contract, cache, manifest helpers
- `docs/skills.md` — skill reference
- `docs/workflows.md` — routing patterns and full-audit sequence
- `docs/interpretation.md` — statistical interpretation rules

See `AGENTS.md` for the full system contract.
