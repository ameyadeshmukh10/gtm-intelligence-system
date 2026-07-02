"""deal_graph — build the deal → contacts → companies → (capped) company-contacts
graph for a date window, using the shared HubSpotClient.

This is the reusable data-acquisition layer for the Marketing-Influence Cohort
Report. It performs the exact pull chain we used to run by hand:

    1. deals created in [start, end] on `pipeline` (search API, paginated)
    2. deals -> contacts associations         (v4 batch)
    3. deals -> companies associations        (v4 batch)
    4. companies -> contacts associations     (v4 batch), capped per company
    5. batch-read every referenced contact + company by id

Raw pulls are cached to data/raw/ via the existing hash_key convention so a
repeat run over the same window is fast and identical.

Returns a DealGraph with tidy dataframes + association maps. Signal detection is
NOT done here — see marketing_signals.py (kept separate so the definitions live
in one place).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .io_contract import hash_key, raw_path
from .marketing_signals import CONTACT_PROPERTIES, COMPANY_PROPERTIES
from ..hubspot.client import HubSpotClient, build_filter_groups, flatten

DEAL_PROPERTIES = [
    "dealname", "pipeline", "dealstage", "amount",
    "createdate", "closedate", "hs_is_closed", "hs_is_closed_won",
    "hubspot_owner_id", "hs_analytics_source",
]


@dataclass
class DealGraph:
    deals: pd.DataFrame                                   # one row per deal
    contacts: pd.DataFrame                                # one row per referenced contact
    companies: pd.DataFrame                               # one row per referenced company
    deal_contacts: dict[str, list[str]] = field(default_factory=dict)   # deal_id -> [contact_id]
    deal_companies: dict[str, list[str]] = field(default_factory=dict)  # deal_id -> [company_id]
    company_contacts: dict[str, list[str]] = field(default_factory=dict)  # company_id -> [contact_id] (capped)
    warnings: list[str] = field(default_factory=list)


def _cache(df: pd.DataFrame, name: str, key_obj) -> str:
    path = raw_path(name, hash_key(key_obj))
    df.to_parquet(path, index=False)
    return str(path)


def build_deal_graph(client: HubSpotClient, *, start: str, end: str,
                     pipeline: str = "default",
                     cap_company_contacts: int = 25,
                     extra_company_properties: list[str] | None = None) -> DealGraph:
    warnings: list[str] = []
    company_props = COMPANY_PROPERTIES + list(extra_company_properties or [])

    # ---- 1. Deals in window --------------------------------------------------
    filters = [{"property": "createdate", "operator": "BETWEEN",
                "value": start, "highValue": end}]
    if pipeline:
        filters.append({"property": "pipeline", "operator": "EQ", "value": pipeline})
    deal_records = list(client.paginate_search(
        "deals", properties=DEAL_PROPERTIES,
        filter_groups=build_filter_groups(filters), limit="all"))
    deals = pd.DataFrame([flatten(r) for r in deal_records])
    if len(deals):
        deals["id"] = deals["id"].astype(str)
    _cache(deals, "deals", {"start": start, "end": end, "pipeline": pipeline})

    if not len(deals):
        warnings.append(f"No deals created in [{start}, {end}] on pipeline={pipeline!r}.")
        return DealGraph(deals=deals, contacts=pd.DataFrame(),
                         companies=pd.DataFrame(), warnings=warnings)

    deal_ids = deals["id"].tolist()

    # ---- 2 & 3. Deal -> contacts / companies ---------------------------------
    deal_contacts = {k: [str(x) for x in v]
                     for k, v in client.associations_batch_read("deals", "contacts", deal_ids).items()}
    deal_companies = {k: [str(x) for x in v]
                      for k, v in client.associations_batch_read("deals", "companies", deal_ids).items()}

    company_ids = sorted({c for v in deal_companies.values() for c in v})

    # ---- 4. Company -> contacts (capped, deterministic) ----------------------
    company_contacts: dict[str, list[str]] = {}
    if company_ids and cap_company_contacts > 0:
        raw_cc = client.associations_batch_read("companies", "contacts", company_ids)
        for cid, contacts in raw_cc.items():
            # deterministic cap: sort by numeric id, take first N
            ordered = sorted({str(x) for x in contacts},
                             key=lambda s: (len(s), s))[:cap_company_contacts]
            company_contacts[cid] = ordered

    # ---- 5. Batch-read all referenced contacts + companies -------------------
    all_contact_ids = sorted(
        {c for v in deal_contacts.values() for c in v}
        | {c for v in company_contacts.values() for c in v})
    all_company_ids = sorted(set(company_ids))

    contacts = pd.DataFrame()
    if all_contact_ids:
        crecs = client.batch_read("contacts", all_contact_ids, CONTACT_PROPERTIES)
        contacts = pd.DataFrame([flatten(r) for r in crecs])
        if len(contacts):
            contacts["id"] = contacts["id"].astype(str)
        _cache(contacts, "influence_contacts",
               {"start": start, "end": end, "pipeline": pipeline,
                "cap": cap_company_contacts})

    companies = pd.DataFrame()
    if all_company_ids:
        corecs = client.batch_read("companies", all_company_ids, company_props)
        companies = pd.DataFrame([flatten(r) for r in corecs])
        if len(companies):
            companies["id"] = companies["id"].astype(str)
        _cache(companies, "influence_companies",
               {"start": start, "end": end, "pipeline": pipeline})

    return DealGraph(
        deals=deals, contacts=contacts, companies=companies,
        deal_contacts=deal_contacts, deal_companies=deal_companies,
        company_contacts=company_contacts, warnings=warnings)
