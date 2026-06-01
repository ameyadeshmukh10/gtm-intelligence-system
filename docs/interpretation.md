# Statistical Interpretation Rules

All agents apply these rules when reasoning over skill outputs. They come from the original EverWorker spec and are replicated here so agents have a single source of truth.

## Significance thresholds

| Statistic | Threshold | Interpretation |
|---|---|---|
| Mann-Whitney p | < 0.05 | Feature has statistically significant difference. Report p + medians — practical size matters as much as significance. |
| Mann-Whitney p | > 0.1 | No meaningful difference. **Do not report as a signal.** |
| Categorical chi² p, n | p<0.05 AND n≥15 | Reliable segment signal. Suitable for targeting recommendations. |
| Categorical | n<15 | **Directional only.** State sample size and note it requires validation. |
| RF AUC | > 0.95 | **Likely data leakage.** Review top features for downstream-of-outcome columns. Flag and re-run with suspects removed. |
| RF AUC | 0.7–0.85 | Useful predictive signal. Features reflect genuine upstream causes. |
| RF AUC | < 0.6 | Poor model fit. Features are not predictive OR target is noisy. |
| SYNERGY delta | > +10pp | Two signals amplify each other. **Routing rule trigger** — prioritize contacts with both signals. |
| SUPPRESSION delta | < −5pp | Two signals cancel. Investigate whether a high-volume program is diluting a quality signal by reaching unqualified audiences. |
| Cluster best/worst ratio | > 3× | Meaningful archetype separation. The gap quantifies the ICP concentration opportunity. |
| Spearman r, p (trend) | \|r\|>0.4 AND p<0.05 | Meaningful directional trend. Investigate what changed in the same period. |
| Spearman r, p (trend) | \|r\|<0.3 OR p>0.1 | **No significant trend.** State "no significant trend detected" — flat is a finding. |

## Inverted medians

When Mann-Whitney reports that the **converted group has a LOWER median** than the non-converted group on a volume metric (emails delivered, sessions, pageviews), this is a signal that **high-volume programs are reaching unqualified audiences**. Do not dismiss — surface explicitly. The implication is that mass-delivery programs are hitting the wrong list.

## Data leakage detection

If RF AUC > 0.95:
1. List the top 5–10 features by importance.
2. Identify features that are downstream consequences of the outcome:
   - `meeting_booked`, `demo_completed`, `sql_created`, `closedate` — these happen only after a deal exists.
   - Any `num_*` count that increments when a deal is created/associated.
3. Exclude them from the feature set (the manifest has a default blacklist — extend via `extra_excluded`).
4. Re-run. Expect AUC to drop to 0.7–0.85 for a real signal.

## Correlation vs causation

Agents should **never** claim causation from observational data. Acceptable phrasing:
- "associated with" ✓
- "correlated with" ✓
- "predicts" ✓ (in the statistical sense)
- "co-occurs with" ✓

Unacceptable phrasing:
- "causes" ✗
- "drives" ✗ (unless a natural experiment or controlled test exists)
- "leads to" ✗

The only times causal language is warranted:
1. A randomized experiment exists.
2. A clean natural experiment exists (e.g. a feature launched on a specific date and the pre/post window is clean).
3. The agent explicitly calls out: "this is correlational evidence; a randomized test of X would confirm."

## Practical vs statistical significance

Both must be present to act on a finding:

- **Statistical significance** — p-value below threshold. Means the pattern is unlikely due to chance alone.
- **Practical significance** — the size of the median/rate gap is meaningful in business terms.

Examples:
- p<0.01, median gap of 0.1 sessions → statistically significant, practically trivial. **Do not act.**
- p=0.08, median gap of 50 sessions → not significant by threshold, but the gap is large. **Flag for validation with more data.**
- p<0.01, median gap of 50 sessions → both significant and practical. **Act.**

## Recency bias in trend data

The most recent month's contacts have had less time to acquire a deal association. A recent dip in conversion rate may reflect data lag, not a real decline. `py_trend_analysis` auto-flags when the latest month's n is <50% of prior months' average — honor that warning.

Rule of thumb: when reporting a trend, **exclude the latest 30 days** from the fit if sales-cycle length is shorter than that; exclude the latest 90 days if cycle is longer.

## Volume spikes and bulk imports

If a single month shows a 5×+ volume spike followed by a return to baseline:
1. Investigate whether an event or bulk import happened that month.
2. Re-compute the trend with and without that month.
3. If the trend disappears when the spike is removed, the spike was an artifact — report this.

## Small-n flags

| n | Label | Action |
|---|---|---|
| n < 5 | Unusable | Do not report |
| 5 ≤ n < 15 | Directional only | Report with "directional" qualifier; do not base strategy on it |
| 15 ≤ n < 50 | Suggestive | Report normally; flag as "would benefit from more data" |
| n ≥ 50 | Robust | Report normally |

When a finding depends on a small-n comparison, state it explicitly: *"n=8 in this segment — directional only."*

## Sample size alongside rates

100% conversion at n=5 is not the same as 100% at n=50. Always report both.

Bad: "Enterprise segment converts at 73%."
Good: "Enterprise segment converts at 73% (n=41)."

## What to do when in doubt

If an analysis result is ambiguous, state:
1. What the data shows.
2. What it does NOT allow you to conclude.
3. What additional data or analysis would resolve it.

Never fill uncertainty with plausible-sounding interpretation.
