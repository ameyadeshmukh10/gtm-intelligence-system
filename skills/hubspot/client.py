"""Shared HubSpot HTTP client. Handles auth, pagination, retries, rate limits.

Workers never touch this directly; they invoke individual hs_pull_* skills.
Each hs_pull_* skill constructs a HubSpotClient and calls one of the helpers
below. All pagination and 429/5xx retry logic lives here, once.
"""
from __future__ import annotations

import os
import time
import logging
from typing import Any, Iterable, Iterator

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("hubspot")

BASE_URL = os.environ.get("HUBSPOT_BASE_URL", "https://api.hubapi.com").rstrip("/")
TOKEN = os.environ.get("HUBSPOT_TOKEN", "")

# Map filter operator synonyms -> HubSpot operator codes
OPERATOR_MAP = {
    "EQ": "EQ", "=": "EQ", "==": "EQ",
    "NEQ": "NEQ", "!=": "NEQ",
    "GT": "GT", ">": "GT",
    "GTE": "GTE", ">=": "GTE",
    "LT": "LT", "<": "LT",
    "LTE": "LTE", "<=": "LTE",
    "BETWEEN": "BETWEEN",
    "IN": "IN", "NOT_IN": "NOT_IN",
    "HAS_PROPERTY": "HAS_PROPERTY", "NOT_HAS_PROPERTY": "NOT_HAS_PROPERTY",
    "CONTAINS_TOKEN": "CONTAINS_TOKEN",
}


class HubSpotError(RuntimeError):
    pass


class HubSpotClient:
    def __init__(self, token: str | None = None, base_url: str | None = None,
                 timeout: int = 30):
        self.token = token or TOKEN
        if not self.token:
            raise HubSpotError("HUBSPOT_TOKEN env var is not set")
        self.base = (base_url or BASE_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # ---------- core request with retry / rate-limit handling ---------- #
    def request(self, method: str, path: str, *, params: dict | None = None,
                json_body: dict | None = None, max_retries: int = 5) -> dict:
        url = f"{self.base}{path}" if path.startswith("/") else f"{self.base}/{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self.session.request(method, url, params=params, json=json_body,
                                         timeout=self.timeout)
            except requests.RequestException as e:
                if attempt >= max_retries:
                    raise HubSpotError(f"Network error after {attempt} attempts: {e}")
                time.sleep(min(2 ** attempt, 30))
                continue

            if r.status_code == 429:
                # HubSpot rate limit. Retry-After header in seconds.
                wait = int(r.headers.get("Retry-After", "10"))
                log.warning("rate-limited; sleeping %ss", wait)
                time.sleep(wait)
                continue
            if 500 <= r.status_code < 600 and attempt < max_retries:
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code >= 400:
                raise HubSpotError(
                    f"{method} {path} -> {r.status_code}: {r.text[:500]}")
            if not r.content:
                return {}
            return r.json()

    # ---------- pagination helpers ---------- #
    def paginate_list(self, path: str, properties: list[str] | None = None,
                      associations: list[str] | None = None,
                      page_size: int = 100, limit: int | str = "all"
                      ) -> Iterator[dict]:
        """GET /crm/v3/objects/{type} style pagination."""
        params: dict[str, Any] = {"limit": page_size}
        if properties:
            params["properties"] = ",".join(properties)
        if associations:
            params["associations"] = ",".join(associations)
        fetched = 0
        after = None
        while True:
            if after:
                params["after"] = after
            data = self.request("GET", path, params=params)
            for item in data.get("results", []):
                yield item
                fetched += 1
                if limit != "all" and fetched >= int(limit):
                    return
            paging = (data.get("paging") or {}).get("next") or {}
            after = paging.get("after")
            if not after:
                return

    def paginate_search(self, object_type: str, *, properties: list[str],
                        filter_groups: list[dict] | None = None,
                        sorts: list[dict] | None = None,
                        associations: list[str] | None = None,
                        page_size: int = 100, limit: int | str = "all"
                        ) -> Iterator[dict]:
        """POST /crm/v3/objects/{type}/search — use when filters are required.

        HubSpot caps the search API at 10,000 total results per query.
        """
        path = f"/crm/v3/objects/{object_type}/search"
        body: dict[str, Any] = {
            "properties": properties,
            "limit": min(page_size, 200),  # search API max
            "filterGroups": filter_groups or [],
            "sorts": sorts or [],
        }
        fetched = 0
        after: str | None = None
        while True:
            if after:
                body["after"] = after
            data = self.request("POST", path, json_body=body)
            results = data.get("results", [])
            for item in results:
                # The search endpoint does not return associations directly;
                # caller must batch-fetch with associations_batch_read if needed.
                yield item
                fetched += 1
                if limit != "all" and fetched >= int(limit):
                    return
            total = data.get("total")
            paging = (data.get("paging") or {}).get("next") or {}
            after = paging.get("after")
            if not after:
                return
            # Search API caps at 10k; break early rather than loop forever.
            if total and fetched >= min(total, 10000):
                return

    # ---------- association helpers ---------- #
    def associations_batch_read(self, from_type: str, to_type: str,
                                ids: list[str]) -> dict[str, list[str]]:
        """Batch resolve associations. Returns {from_id: [to_id,...]}."""
        out: dict[str, list[str]] = {}
        path = f"/crm/v4/associations/{from_type}/{to_type}/batch/read"
        # Batch size limit is 1000 inputs per request
        for chunk in _chunks(ids, 1000):
            body = {"inputs": [{"id": x} for x in chunk]}
            data = self.request("POST", path, json_body=body)
            for rec in data.get("results", []):
                src = rec["from"]["id"]
                targets = [t["toObjectId"] for t in rec.get("to", [])]
                out[src] = targets
        return out

    # ---------- object-batch read by id ---------- #
    def batch_read(self, object_type: str, ids: list[str],
                   properties: list[str]) -> list[dict]:
        path = f"/crm/v3/objects/{object_type}/batch/read"
        out: list[dict] = []
        for chunk in _chunks(ids, 100):
            body = {"properties": properties,
                    "inputs": [{"id": x} for x in chunk]}
            data = self.request("POST", path, json_body=body)
            out.extend(data.get("results", []))
        return out


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def build_filter_groups(filters: list[dict] | None) -> list[dict]:
    """Translate a flat filter list into HubSpot's filterGroups format.

    Input filter:  {"property": "...", "operator": "...", "value": ...}
    Output:        wrapped into a single filterGroup (AND semantics).
    Pass multiple filterGroups (OR between groups) directly if you need OR.
    """
    if not filters:
        return []
    translated = []
    for f in filters:
        op = OPERATOR_MAP.get(str(f.get("operator", "EQ")).upper(), "EQ")
        flt = {"propertyName": f["property"], "operator": op}
        if op == "BETWEEN":
            flt["value"] = f.get("value")
            flt["highValue"] = f.get("highValue") or f.get("high")
        elif op in {"IN", "NOT_IN"}:
            flt["values"] = f.get("values") or f.get("value") or []
        elif op not in {"HAS_PROPERTY", "NOT_HAS_PROPERTY"}:
            flt["value"] = f.get("value")
        translated.append(flt)
    return [{"filters": translated}]


def flatten(obj: dict, extra: dict | None = None) -> dict:
    """Flatten a HubSpot object record (id + properties) into a flat dict."""
    out: dict[str, Any] = {"id": obj.get("id")}
    out.update(obj.get("properties", {}) or {})
    # Flatten simple associations (v3 results field)
    assoc = obj.get("associations") or {}
    for atype, payload in assoc.items():
        results = (payload or {}).get("results") or []
        out[f"assoc_{atype}_ids"] = ";".join(r.get("id", "") for r in results)
    if extra:
        out.update(extra)
    return out
