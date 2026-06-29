"""load_spend — ingest external marketing SPEND and normalize for MMM.

MMM is the only GTM skill that needs marketing *spend* (dollars by channel by
period). HubSpot does not store ad spend, so this skill ingests it from an
external export (finance CSV, ad-platform pull, a spreadsheet) and normalizes it
to a tidy long table that py_mmm_features can join onto the deals-created series.

Accepts either a "long" file (one row per period+channel) or a "wide" file (one
column per channel). Channels are mapped to a small set of MODELING GROUPS — you
cannot identify 11 channels from ~17 monthly points, so grouping is required, not
cosmetic (see Part 1.B / Part 4 of the blueprint).

Params (JSON):
  input:        str                 path to spend CSV/parquet (required)
  period_col:   str                 default "period" — a date or YYYY-MM / YYYY-Www string
  granularity:  "month" | "week"    default "month" — resample target
  layout:       "long" | "wide"     default auto-detect
  channel_col:  str                 long layout: column holding the channel name (default "channel")
  spend_col:    str                 long layout: column holding spend (default "spend")
  exposure_cols: list[str]          optional extra metrics to carry (impressions, clicks)
  channel_map:  dict                {raw_channel: group}; unmapped channels -> "other_paid".
                                    Map a channel to null (or "__exclude__") to DROP it from the model
                                    (e.g. brand/operating spend that isn't a demand channel).
  drop_channels: list[str]          raw channel names to exclude entirely before aggregation
  groups:       list[str]           allowed modeling groups (informational; default common set)

Output: long parquet (period, channel_group, spend[, impressions, clicks]) cached
in data/raw/, plus the usual JSON envelope.
"""
from __future__ import annotations

import pandas as pd

from .io_contract import emit, emit_error, hash_key, raw_path, read_params

# Sensible default mapping from raw HubSpot/ad-platform channel labels to the
# 4-6 modeling groups an MMM can actually separate. Override via params.channel_map.
DEFAULT_CHANNEL_MAP = {
    "paid_search": "paid_search", "paid search": "paid_search",
    "google_ads": "paid_search", "sem": "paid_search", "search": "paid_search",
    "paid_social": "paid_social", "paid social": "paid_social",
    "linkedin": "paid_social", "linkedin_ads": "paid_social",
    "youtube": "paid_social", "youtube_ads": "paid_social",
    "youtube advertising": "paid_social",
    "facebook": "paid_social", "meta": "paid_social",
    "outbound": "outbound", "sdr": "outbound", "bdr": "outbound",
    "events": "events_field", "event": "events_field",
    "field": "events_field", "conference": "events_field", "webinar": "events_field",
    "email": "email_nurture", "email_marketing": "email_nurture",
    "nurture": "email_nurture", "nurtures": "email_nurture",
    "content": "content_syndication", "content_syndication": "content_syndication",
    "syndication": "content_syndication",
}

DEFAULT_GROUPS = ["paid_search", "paid_social", "events_field",
                  "email_nurture", "content_syndication", "other_paid"]


def _to_period(series: pd.Series, granularity: str) -> pd.Series:
    """Coerce a date/period-string column to a normalized period start timestamp."""
    dt = pd.to_datetime(series, errors="coerce", utc=True)
    if dt.isna().mean() > 0.5:
        # Maybe already period strings like 2025-07 or 2025-W30 — let pandas try.
        dt = pd.to_datetime(series.astype(str), errors="coerce", utc=True)
    freq = "W-MON" if granularity == "week" else "MS"
    return dt.dt.tz_localize(None).dt.to_period(
        "W" if granularity == "week" else "M").dt.start_time


def main():
    try:
        p = read_params()
        if not p.get("input"):
            emit_error("input (path to spend CSV/parquet) is required")
            return
        path = p["input"]
        granularity = p.get("granularity", "month")
        period_col = p.get("period_col", "period")
        exposure_cols = p.get("exposure_cols") or []
        cmap = {**DEFAULT_CHANNEL_MAP, **{k.lower(): v
                                          for k, v in (p.get("channel_map") or {}).items()}}

        df = pd.read_parquet(path) if str(path).endswith(".parquet") \
            else pd.read_csv(path)
        if period_col not in df.columns:
            emit_error(f"period_col '{period_col}' not in spend file columns "
                       f"{list(df.columns)}")
            return

        layout = p.get("layout") or (
            "long" if (p.get("channel_col", "channel") in df.columns) else "wide")

        if layout == "long":
            channel_col = p.get("channel_col", "channel")
            spend_col = p.get("spend_col", "spend")
            for c in (channel_col, spend_col):
                if c not in df.columns:
                    emit_error(f"long layout needs column '{c}'")
                    return
            keep = [period_col, channel_col, spend_col] + \
                   [c for c in exposure_cols if c in df.columns]
            long = df[keep].rename(columns={channel_col: "channel",
                                            spend_col: "spend"})
        else:  # wide: every non-period, non-exposure numeric column is a channel
            id_cols = [period_col] + [c for c in exposure_cols if c in df.columns]
            chan_cols = [c for c in df.columns if c not in id_cols
                         and pd.api.types.is_numeric_dtype(df[c])]
            long = df.melt(id_vars=[period_col], value_vars=chan_cols,
                           var_name="channel", value_name="spend")

        long["_period"] = _to_period(long[period_col], granularity)

        # Map raw channels -> modeling groups, with explicit exclusion support.
        # A channel is DROPPED if it is listed in drop_channels, or mapped to
        # null / an "exclude" sentinel — used for brand/operating spend that is
        # real money but not a demand channel we want the MMM to attribute.
        DROP_SENTINELS = {"__exclude__", "exclude", "drop", "none", "null", "nan"}
        drop_set = {str(c).strip().lower() for c in (p.get("drop_channels") or [])}
        raw = long["channel"].astype(str).str.strip().str.lower()

        def _to_group(ch: str):
            if ch in drop_set:
                return None
            g = cmap.get(ch, "other_paid")
            if g is None or str(g).strip().lower() in DROP_SENTINELS:
                return None
            return g

        long["channel_group"] = raw.map(_to_group)
        dropped = sorted(raw[long["channel_group"].isna()].unique().tolist())
        long = long[long["channel_group"].notna()].copy()
        long["spend"] = pd.to_numeric(long["spend"], errors="coerce").fillna(0.0)

        agg = {"spend": "sum"}
        for c in exposure_cols:
            if c in long.columns:
                long[c] = pd.to_numeric(long[c], errors="coerce").fillna(0.0)
                agg[c] = "sum"
        out = (long.dropna(subset=["_period"])
                   .groupby(["_period", "channel_group"], as_index=False).agg(agg)
                   .rename(columns={"_period": "period"})
                   .sort_values(["period", "channel_group"]))

        warnings: list[str] = []
        if dropped:
            warnings.append(
                f"Excluded {len(dropped)} channel(s) from the model: {dropped}. "
                f"Their spend is not attributed (treated as brand/operating).")
        n_periods = out["period"].nunique()
        # Sparsity: a channel that is zero in >50% of periods is unreliable in MMM.
        for grp, sub in out.groupby("channel_group"):
            present = out[out.channel_group == grp].set_index("period")["spend"]
            zero_frac = float((present <= 0).mean()) if len(present) else 1.0
            if zero_frac > 0.5:
                warnings.append(
                    f"Channel group '{grp}' is zero in {zero_frac:.0%} of periods "
                    f"— too sparse to estimate; consider merging or dropping it.")
        if n_periods < 12:
            warnings.append(
                f"Only {n_periods} {granularity}s of spend — below the ~12 minimum "
                f"for a credible multi-channel MMM. Treat results as directional.")

        key = hash_key({"path": str(path), "gran": granularity, "map": cmap})
        outfile = raw_path("spend", key)
        out.to_parquet(outfile, index=False)

        totals = out.groupby("channel_group")["spend"].sum().round(0).to_dict()
        emit({
            "results": {
                "granularity": granularity,
                "n_periods": int(n_periods),
                "channel_groups": sorted(out["channel_group"].unique().tolist()),
                "total_spend_by_group": {k: float(v) for k, v in totals.items()},
                "excluded_channels": dropped,
                "period_range": [str(out["period"].min()), str(out["period"].max())],
            },
            "summary": (
                f"Normalized spend into {out['channel_group'].nunique()} channel "
                f"groups across {n_periods} {granularity}s "
                f"({out['period'].min():%Y-%m} to {out['period'].max():%Y-%m}). "
                f"Cached at {outfile}."),
            "metadata": {
                "n_records": int(len(out)),
                "n_features": int(out["channel_group"].nunique()),
                "warnings": warnings,
                "artifacts": {"parquet": str(outfile)},
            },
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
