"""hs_pull_deals — retrieve Deal records with stage dates, pipeline, territory, owner.

Params (JSON):
  properties:   list[str]   defaults to a practical GTM set
  filters:      list[dict]  {property, operator, value}
  associations: list[str]   e.g. ["contacts","companies"]
  limit:        int | "all" (default "all")
"""
from ._shared import run_object_pull
from .client import HubSpotClient
from ..common.io_contract import read_params


DEFAULT_PROPERTIES = [
    # Identity + pipeline
    "dealname", "pipeline", "dealstage", "amount", "hs_deal_stage_probability",
    "createdate", "closedate", "hs_lastmodifieddate", "hs_is_closed", "hs_is_closed_won",
    # Ownership / territory
    "hubspot_owner_id", "territory", "deal_currency_code",
    # Firmographics commonly copied to deal
    "industry_group", "industry", "revenue_range", "company_size",
    # Source & attribution
    "hs_analytics_source", "hs_analytics_source_data_1", "hs_analytics_source_data_2",
    # Stage entry dates — these vary per instance; use hs_pull_custom_properties
    # to discover the exact dealstage-date property names and extend this list.
    "hs_date_entered_appointmentscheduled",
    "hs_date_entered_qualifiedtobuy",
    "hs_date_entered_presentationscheduled",
    "hs_date_entered_decisionmakerboughtin",
    "hs_date_entered_contractsent",
    "hs_date_entered_closedwon",
    "hs_date_entered_closedlost",
]


if __name__ == "__main__":
    params = read_params()
    run_object_pull(object_type="deals", params=params,
                    default_properties=DEFAULT_PROPERTIES)
