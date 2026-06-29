"""Shared Jinja2 templates object + a small render helper with common context."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _fromjson(val):
    import json
    if not val:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


templates.env.filters["fromjson"] = _fromjson

NAV = [
    ("/", "Overview"),
    ("/mmm", "Marketing Mix"),
    ("/mmm/whatif", "Budget What-if"),
    ("/data", "Data & Runs"),
    ("/audit", "GTM Audit"),
]


def render(request: Request, template: str, **ctx):
    return templates.TemplateResponse(
        template, {"request": request, "nav": NAV, "active": request.url.path, **ctx})
