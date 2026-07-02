"""influence_report — repeatable Marketing-Influence Cohort Report.

ONE deterministic skill that does the entire chain we used to run by hand:
pull deals in a window -> resolve associated contacts + companies + capped
company-contacts -> detect organic/direct/blog signals -> roll up to deal level
-> bucket deals by createdate into week/month/quarter cohorts -> report
influenced-vs-cold deal count + pipeline $ per period + sub-signal breakdown.

The agent makes ONE call; the web app enqueues ONE job. Signal definitions are
locked in skills/common/marketing_signals.py. No randomness -> byte-identical
results for identical params.

Params (JSON):
  start:        str   (required) ISO date, inclusive  (deal createdate >=)
  end:          str   (required) ISO date, inclusive  (deal createdate <=)
  granularity:  "week" | "month" | "quarter"   default "month"
  pipeline:     str   default "default"
  cap_company_contacts: int   default 25   (per-company contact cap; 0 disables)
  run_id:       str   optional; default influence_<granularity>_<start>_<end>

Outputs:
  data/features/<run_id>.parquet            one row per deal + signal flags
  data/features/<run_id>_manifest.json      report manifest
  data/results/<run_id>_influence_report.json   full results (also emitted)
"""
from __future__ import annotations

import re

import pandas as pd

from ..common.io_contract import (emit, emit_error, features_path,
                                   manifest_path, read_params, results_path,
                                   write_json)
from ..common.deal_graph import build_deal_graph
from ..common.marketing_signals import (contact_signal_flags,
                                        company_signal_flags,
                                        discover_linkedin_organic_props,
                                        target_definition_text)
from ..hubspot.client import HubSpotClient

_GRANULARITY = {"week": "W", "month": "M", "quarter": "Q"}


def _period_start(series: pd.Series, granularity: str) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)
    return dt.dt.to_period(_GRANULARITY[granularity]).dt.start_time


def _default_run_id(granularity: str, start: str, end: str) -> str:
    safe = lambda s: re.sub(r"[^0-9]", "", s)[:8]
    return f"influence_{granularity}_{safe(start)}_{safe(end)}"


def main():
    try:
        p = read_params()
        start, end = p.get("start"), p.get("end")
        if not start or not end:
            emit_error("start and end (ISO dates) are required")
            return
        granularity = p.get("granularity", "month")
        if granularity not in _GRANULARITY:
            emit_error(f"granularity must be one of {sorted(_GRANULARITY)}")
            return
        pipeline = p.get("pipeline", "default")
        cap = int(p.get("cap_company_contacts", 25))
        run_id = p.get("run_id") or _default_run_id(granularity, start, end)

        client = HubSpotClient()

        # ---- Discover instance-specific LinkedIn-organic company props -------
        li_props = discover_linkedin_organic_props(client)

        # ---- Build the deal graph (deals -> contacts/companies/company-contacts)
        g = build_deal_graph(client, start=start, end=end, pipeline=pipeline,
                             cap_company_contacts=cap,
                             extra_company_properties=li_props)
        warnings = list(g.warnings)

        if not len(g.deals):
            emit_error(f"No deals created in [{start}, {end}] on pipeline={pipeline!r}",
                       warnings=warnings)
            return

        # ---- Signal flags on contacts + companies ---------------------------
        contacts = contact_signal_flags(g.contacts) if len(g.contacts) else g.contacts
        companies = company_signal_flags(g.companies, li_props) if len(g.companies) else g.companies

        c_influenced = set(contacts.loc[contacts["influenced"], "id"]) if len(contacts) else set()
        c_organic = set(contacts.loc[contacts["sig_organic"], "id"]) if len(contacts) else set()
        c_direct = set(contacts.loc[contacts["sig_direct"], "id"]) if len(contacts) else set()
        c_blog = set(contacts.loc[contacts["sig_blog"], "id"]) if len(contacts) else set()

        co_influenced = set(companies.loc[companies["influenced"], "id"]) if len(companies) else set()
        co_organic = set(companies.loc[companies["sig_organic"], "id"]) if len(companies) else set()
        co_direct = set(companies.loc[companies["sig_direct"], "id"]) if len(companies) else set()
        co_li = set(companies.loc[companies["sig_li_organic"], "id"]) if len(companies) else set()

        # ---- Roll signals up to the deal ------------------------------------
        deals = g.deals.copy()
        deals["amount"] = pd.to_numeric(deals.get("amount"), errors="coerce").fillna(0.0)
        deals["period"] = _period_start(deals["createdate"], granularity)
        deals = deals.dropna(subset=["period"])

        def deal_flags(deal_id: str) -> dict:
            direct_contacts = set(g.deal_contacts.get(deal_id, []))
            companies_of = set(g.deal_companies.get(deal_id, []))
            company_contacts = set()
            for co in companies_of:
                company_contacts |= set(g.company_contacts.get(co, []))
            contacts_all = direct_contacts | company_contacts
            f_contact_organic = bool(contacts_all & c_organic)
            f_contact_direct = bool(contacts_all & c_direct)
            f_contact_blog = bool(contacts_all & c_blog)
            f_co_organic = bool(companies_of & co_organic)
            f_co_direct = bool(companies_of & co_direct)
            f_co_li = bool(companies_of & co_li)
            influenced = bool((contacts_all & c_influenced) or (companies_of & co_influenced))
            return {
                "sig_contact_organic": f_contact_organic,
                "sig_contact_direct": f_contact_direct,
                "sig_contact_blog": f_contact_blog,
                "sig_company_organic": f_co_organic,
                "sig_company_direct": f_co_direct,
                "sig_company_li_organic": f_co_li,
                "influenced": influenced,
                "n_deal_contacts": len(direct_contacts),
                "n_company_contacts": len(company_contacts),
            }

        flags = pd.DataFrame([deal_flags(d) for d in deals["id"]], index=deals.index)
        deals = pd.concat([deals, flags], axis=1)

        # ---- Detail parquet + manifest --------------------------------------
        keep = ["id", "dealname", "amount", "period", "dealstage",
                "hs_is_closed", "hs_is_closed_won", "hubspot_owner_id",
                "sig_contact_organic", "sig_contact_direct", "sig_contact_blog",
                "sig_company_organic", "sig_company_direct", "sig_company_li_organic",
                "influenced", "n_deal_contacts", "n_company_contacts"]
        detail = deals[[c for c in keep if c in deals.columns]].copy()
        detail_path = features_path(run_id)
        detail.to_parquet(detail_path, index=False)

        # ---- Per-period cohort table ----------------------------------------
        periods = []
        for period, sub in deals.sort_values("period").groupby("period"):
            infl = sub[sub["influenced"]]
            cold = sub[~sub["influenced"]]
            n = len(sub)
            amt = float(sub["amount"].sum())
            periods.append({
                "period": period.strftime("%Y-%m-%d"),
                "total_deals": n,
                "total_pipeline_usd": amt,
                "influenced_deals": int(len(infl)),
                "influenced_pipeline_usd": float(infl["amount"].sum()),
                "cold_deals": int(len(cold)),
                "cold_pipeline_usd": float(cold["amount"].sum()),
                "influence_rate_deals": (len(infl) / n) if n else 0.0,
                "influence_rate_pipeline": (float(infl["amount"].sum()) / amt) if amt else 0.0,
            })

        # ---- Sub-signal breakdown (deal-level, overlapping) -----------------
        sub_signals = []
        for col, label in [
            ("sig_contact_organic", "contact organic search"),
            ("sig_contact_direct", "contact direct traffic"),
            ("sig_contact_blog", "contact blog activity"),
            ("sig_company_organic", "company organic search"),
            ("sig_company_direct", "company direct traffic"),
            ("sig_company_li_organic", "company LinkedIn organic"),
        ]:
            m = deals[deals[col]]
            sub_signals.append({
                "signal": label,
                "deals": int(len(m)),
                "pipeline_usd": float(m["amount"].sum()),
            })

        # ---- Totals + sanity (Rule 3) ---------------------------------------
        n_total = int(len(deals))
        n_infl = int(deals["influenced"].sum())
        amt_total = float(deals["amount"].sum())
        amt_infl = float(deals.loc[deals["influenced"], "amount"].sum())
        sanity_ok = n_infl <= n_total
        if not sanity_ok:
            warnings.append("SANITY FAIL: influenced deals exceed total deals.")
        if not li_props:
            warnings.append("Fibbler LinkedIn-organic company properties not found "
                            "in this instance — company-LinkedIn-organic signal skipped.")

        results = {
            "run_id": run_id,
            "target_definition": target_definition_text(cap),
            "window": {"start": start, "end": end, "granularity": granularity,
                       "pipeline": pipeline, "cap_company_contacts": cap},
            "totals": {
                "n_deals": n_total,
                "pipeline_usd": amt_total,
                "influenced_deals": n_infl,
                "influenced_pipeline_usd": amt_infl,
                "cold_deals": n_total - n_infl,
                "cold_pipeline_usd": amt_total - amt_infl,
                "pct_deals_influenced": (n_infl / n_total) if n_total else 0.0,
                "pct_pipeline_influenced": (amt_infl / amt_total) if amt_total else 0.0,
            },
            "periods": periods,
            "sub_signals": sub_signals,
            "sanity": {"n_deals": n_total, "influenced_deals": n_infl,
                       "ratio_ok": bool(sanity_ok)},
            "linkedin_organic_props": li_props,
        }
        out_json = results_path(run_id, "influence_report")
        write_json(out_json, results)

        manifest = {
            "run_id": run_id,
            "kind": "influence_report",
            "granularity": granularity,
            "features_path": str(detail_path),
            "date": ["period"],
            "target": "influenced",
            "window": results["window"],
            "n_deals": n_total,
        }
        write_json(manifest_path(run_id), manifest)

        emit({
            "results": results,
            "summary": (
                f"{granularity.title()} influence report, {start}..{end}: "
                f"{n_infl}/{n_total} deals ({results['totals']['pct_deals_influenced']:.1%}) "
                f"and ${amt_infl:,.0f}/${amt_total:,.0f} pipeline "
                f"({results['totals']['pct_pipeline_influenced']:.1%}) touched by "
                f"organic/direct/blog signals across {len(periods)} periods."),
            "metadata": {
                "n_records": n_total, "n_features": len(sub_signals),
                "warnings": warnings,
                "artifacts": {"features": str(detail_path),
                              "manifest": str(manifest_path(run_id)),
                              "json": str(out_json)},
            },
        })
    except Exception as e:
        emit_error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
