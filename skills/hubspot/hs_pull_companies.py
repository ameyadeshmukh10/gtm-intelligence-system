"""hs_pull_companies — firmographics by industry, revenue, size, territory."""
from ._shared import run_object_pull
from ..common.io_contract import read_params


DEFAULT_PROPERTIES = [
    "name", "domain", "industry", "industry_group", "annualrevenue",
    "revenue_range", "numberofemployees", "company_size",
    "country", "state", "region", "territory",
    "lifecyclestage", "type", "hs_lead_status",
    "createdate", "hs_lastmodifieddate",
]


if __name__ == "__main__":
    params = read_params()
    run_object_pull(object_type="companies", params=params,
                    default_properties=DEFAULT_PROPERTIES)
