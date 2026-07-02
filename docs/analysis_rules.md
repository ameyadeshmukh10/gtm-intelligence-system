# Analytical Rules — MUST follow

This file is the authoritative source for target definitions and cohort construction in this repo. Every agent and every contact-level analysis must pass these rules before reporting any rate, lift, or trend.

## Rule 1 — Opportunity / deal-target definition

### The rule

A contact is counted as an opportunity ONLY when BOTH conditions hold:

1. **`lifecyclestage ∈ {opportunity, salesqualifiedlead, customer}`** — the sales-side lifecycle stages.
2. **AND the contact is associated with a deal** (present in the deal→contact association set for the window being analyzed).

Set intersection. Not union.

```python
has_opp = (
    df['lifecyclestage'].isin({'opportunity', 'salesqualifiedlead', 'customer'})
    & df['id_str'].isin(deal_associated_contact_ids)
)
```

### What is NOT an opportunity

- `lifecyclestage ∈ {lead, marketingqualifiedlead, other}` — these are marketing/top-of-funnel statuses.
- `lifecyclestage = <numeric_id>` — custom pipeline stages with numeric IDs (e.g. `2265264367`). These are almost always marketing-workflow stages (HubSpot often labels them "Sales Accepted Lead" or similar). **Never include a custom numeric stage in an opportunity target without first resolving its label and confirming with the user** that it represents a sales-accepted state.
- "Contact is on a deal" alone — a contact can be associated with a deal that was closed-lost, disqualified, or abandoned. Require the sales-lifecycle coincidence.
- "Contact reached lifecycle=opportunity" alone — HubSpot automations can bump contacts to opportunity stage without an actual deal ever being created. Require the deal-association coincidence.

### Why (past incident)

In the Apr 20 2026 filtered audit, a target defined as `lifecyclestage ∈ {opportunity, SQL, customer, 2265264367} OR in_deal_assoc` produced a 46.67% "opportunity rate" against 10,951 contacts. The actual deal count in window was 502. Breakdown:

- 188 contacts actually on deals (the real signal)
- 4,899 contacts in custom stage `2265264367` (labeled "Sales Accepted Lead")
- 5,111 total positives under the inflated target

The reported rates (74% LinkedIn Lead Gen, 90% demo popup × paid social) were rates of **SAL advancement**, not rates of deal production. Correct rates under the intersection rule: LinkedIn Lead Gen 0.69% (below baseline), demo popup 20%, MeetingsLink 48%.

**Sanity check every contact-level target before reporting.** If `positive_count > 2 × n_deals_in_window`, the target definition is wrong.

### How to apply

1. Pull the deal-association set for the window first (`hs_pull_associations` from deals→contacts using the windowed deal parquet).
2. Build the target via intersection with a strict lifecyclestage set.
3. Print `positive_count`, `n_contacts`, and `n_deals_in_window` side-by-side before running any downstream analysis.
4. If a custom numeric lifecyclestage ID appears in the data, call `hs_pull_custom_properties` with `object_type="contacts", filter_name="lifecyclestage", show_options=true` to resolve the label, and confirm with the user whether it counts as a sales-accepted state before including it.

---

## Rule 2 — Trend analysis must segment by `recent_conversion_event_name`

### The rule

Global monthly trend on `has_opp` tells you whether your biggest program is growing or shrinking. It does NOT tell you which individual programs are improving or degrading.

Every contact-level trend analysis must produce a **two-dimensional grid**: month × `recent_conversion_event_name` bucket, with per-cell rate and volume.

```python
# After bucketing recent_conversion_event_name into program categories
pivot = (df.groupby([df['createdate'].dt.to_period('M'), 'recent_bucket'])
           .agg(n=('has_opp','size'), conv=('has_opp','sum'))
           .assign(rate=lambda x: x['conv']/x['n']))
```

Per-bucket trend detection via Spearman vs month index is required for any bucket with ≥ 5 months of data and n ≥ 20 per month.

### Why

Global trends aggregate across all programs. If LinkedIn Lead Gen doubles in volume at 0.7% conversion, the global rate drops even if every individual program improved. Conversely, if LinkedIn Lead Gen collapses while demo popup doubles, the global rate can stay flat while the program mix changes radically.

A program-level trend grid surfaces:
- Which programs are improving in quality over time
- Which programs are ramping in volume
- Which programs are new (appearing mid-window)
- Which programs are dying (disappearing)
- Whether an aggregate rate change is composition or individual-program change

### How to apply

1. After bucketing `recent_conversion_event_name` into program categories (linkedin_leadgen, demo_popup, meetings_link, contact_sales, academy, webinar, event, content_download, newsletter, other), build the pivot table above.
2. Report per-bucket monthly rates alongside aggregate rate in every trend output.
3. For each bucket with ≥5 months and ≥20/month volume, report per-bucket Spearman r, p, and direction.
4. In the final synthesis, state explicitly: "Aggregate rate moved from X% to Y%. Of that, Z was composition shift (which programs grew/shrank) and W was within-program change."

### Minimum columns in every trend output

| month | total_n | total_conv | total_rate | {bucket}_n | {bucket}_conv | {bucket}_rate | ... |

---

## Rule 3 — Sanity-check every reported rate against population ground truth

Before reporting any rate or lift:

1. **Multiply back out.** If a segment has n=5,000 and rate=74%, the implied positive count is 3,700. Does that number make sense against the total universe?
2. **Check against known constraints.** If there are 502 deals in window and a report claims 5,111 contacts produced opportunities, the math is wrong — either the target is inflated or the denominator is wrong.
3. **Flag order-of-magnitude mismatches.** If any reported rate is 10×+ above what the population size can support, stop and re-examine the target.

This is a hard gate: no rate above the plausible ceiling given deal count gets reported.

---

## Rule 4 — Custom lifecycle / stage IDs must be resolved before use

Any numeric lifecyclestage ID (e.g. `2265264367`) or custom pipeline stage that appears in data must be resolved to its human label via `hs_pull_custom_properties` before being used in any target. Never assume a numeric stage means "opportunity" based on frequency or position.

---

## Rule 5 — `source_offline` is NOT a marketing channel

### The rule

`source_offline` contacts are either event captures (attendee lists imported post-event) or outbound prospecting imports done by reps. They do NOT represent an inbound marketing program. **Exclude `source_offline = 1` from any marketing attribution analysis.**

Marketing attribution cohort should be:
```python
marketing_cohort = (df['recent_conversion_date'].notna()) & (df['source_offline'] == 0)
```

### What counts as a marketing program signal

- **Content**: content downloads (PDFs, ebooks, whitepapers)
- **Events**: webinars, events, summits — via inbound sign-up forms (NOT offline-captured attendee lists)
- **Inbound demo requests**: demo popup form, contact sales form, meetings link booked directly
- **Lead score**: `hubspotscore` or equivalent
- **Site engagement**: `hs_analytics_num_page_views`, `hs_analytics_num_visits`
- **Email engagement**: `hs_email_open`, `hs_email_click`, `hs_email_delivered`
- **Academy registrations**
- **Email nurtures completed**
- **Traffic sources** (all values of `hs_analytics_source` except OFFLINE): PAID_SOCIAL, PAID_SEARCH, ORGANIC_SEARCH, DIRECT_TRAFFIC, EMAIL_MARKETING, REFERRALS, SOCIAL_MEDIA

### Why

In this HubSpot instance, reps import prospecting lists and event attendee lists as OFFLINE-sourced contacts. Treating those as inbound marketing wins makes "OFFLINE" look like a top channel when it's actually manual rep work. Marketing performance should be measured against marketing-owned acquisition only.

### How to apply

1. After pulling contacts, add a `source_offline` flag from `hs_analytics_source = 'OFFLINE'`.
2. Build the marketing cohort as the intersection above.
3. Report rep-sourced (`source_offline = 1`) contacts in a separate "Rep acquisition" section if their volume is operationally relevant, but never mix them into channel rankings or SYNERGY/SUPPRESSION calculations for marketing.

---

## Rule 6 — Report the target definition alongside every rate

Every analytical output that contains a rate must include the target definition that produced it, in plain English, at the top. Example:

> Target: `has_opp` = contact is associated with a deal in the windowed deal set AND lifecyclestage ∈ {opportunity, salesqualifiedlead, customer}. n_positive = 184 of 10,951 contacts (1.68% baseline).

If the reader can't reconstruct the target from the output, the output is not usable.

---

## Appendix — Marketing-Influence signal definition (LOCKED)

The Marketing-Influence Cohort Report (`skills/reports/influence_report.py`, driven by the
`marketing-influence-analyst` agent and the web app `/influence` surface) uses a **fixed, non-negotiable**
definition of "influence." It lives in code at `skills/common/marketing_signals.py` — the single source
of truth. Do not redefine it per analysis.

A deal is **influenced** if ANY node in its graph carries ANY of these organic/direct/blog signals:

- **Contact** (deal-direct contacts AND up to N=25 of each associated company's other contacts):
  - `hs_analytics_source` ∈ {ORGANIC_SEARCH, DIRECT_TRAFFIC}, or `hs_latest_source` ∈ {ORGANIC_SEARCH, DIRECT_TRAFFIC}
  - blog activity: `hs_analytics_first_url` / `hs_analytics_last_url` / `hs_analytics_first_referrer` / `hs_analytics_last_referrer` contains "blog"
- **Company** (the deal's associated companies):
  - `hs_analytics_source` ∈ {ORGANIC_SEARCH, DIRECT_TRAFFIC}
  - LinkedIn organic: `fibbler_linkedin_organic_(impressions|engagements)_<acct>_90_days` > 0 (account id discovered dynamically, never hardcoded)

**Cohort** = influenced vs cold, bucketed by deal `createdate` at week / month / quarter granularity.
Metric = deal count + pipeline $ per cohort per period.

**Compliance:** ORGANIC_SEARCH and DIRECT_TRAFFIC are mutually exclusive with OFFLINE, so this cohort is
clean under Rule 5 by construction. Blog visits are counted as genuine site engagement. The report emits
its target definition string (Rule 6) and a sanity line (influenced ≤ total; Rule 3) in every run.
Caveat every report: blog activity is undercounted because HubSpot stores only first/last URL per contact.
