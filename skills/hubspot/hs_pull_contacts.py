"""hs_pull_contacts — retrieve contact firmographics, program enrollment, engagement.

Params (JSON):
  properties: list[str]
  filters: list[dict]
  associations: list[str]
  limit: int | "all"
"""
from ._shared import run_object_pull
from ..common.io_contract import read_params


DEFAULT_PROPERTIES = [
    # Identity
    "email", "firstname", "lastname", "jobtitle", "seniority",
    "employment_role", "job_function",
    # Firmographics
    "company", "industry", "revenue_range", "num_employees", "territory", "country", "region",
    "lifecyclestage", "hs_lead_status",
    # Deal association rollup
    "num_associated_deals",
    # Engagement metrics
    "hs_analytics_num_visits", "hs_analytics_num_page_views",
    "hs_analytics_num_event_completions", "hs_email_open",
    "hs_email_click", "hs_email_delivered", "num_conversion_events",
    "hs_analytics_source", "hs_analytics_first_url", "hs_analytics_first_referrer",
    "first_conversion_event_name", "recent_conversion_event_name",
    "first_conversion_date", "recent_conversion_date",
    # Program enrollment (these are usually custom, semicolon-delimited)
    "events_attended", "webinars_attended", "email_nurtures_enrolled",
    "content_downloads", "pdf_downloads", "academy_registrations",
    "inbound_requests", "linkedin_lead_gen",
    # Dates
    "createdate", "lastmodifieddate",
]


if __name__ == "__main__":
    params = read_params()
    run_object_pull(object_type="contacts", params=params,
                    default_properties=DEFAULT_PROPERTIES)
