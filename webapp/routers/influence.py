"""Marketing-Influence Cohort Report surface.

GET  /influence            — controls + saved-runs list + latest (or selected) render
POST /influence/run        — enqueue the influence_report skill as a background job
GET  /influence/view       — render a specific saved run (?run_id=)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form
from starlette.requests import Request
from starlette.responses import RedirectResponse

from .. import db, jobs
from ..services import influence_service
from ..templating import render

router = APIRouter()

_MODULE = "skills.reports.influence_report"


def _view(request: Request, run_id: Optional[str]):
    res = influence_service.load_result(run_id) if run_id else influence_service.latest()
    charts = influence_service.to_charts(res) if res else None
    runs = influence_service.list_runs()
    recent = db.query("SELECT id, label, status, error, created_at, finished_at "
                      "FROM job WHERE name=? ORDER BY id DESC LIMIT 10", (_MODULE,))
    return render(request, "influence.html", res=res, charts=charts,
                  runs=runs, jobs=recent)


@router.get("/influence")
def influence_page(request: Request, run_id: Optional[str] = None):
    return _view(request, run_id)


@router.get("/influence/view")
def influence_view(request: Request, run_id: str):
    return _view(request, run_id)


@router.post("/influence/run")
def influence_run(request: Request,
                  start: str = Form(...), end: str = Form(...),
                  granularity: str = Form("month"),
                  cap_company_contacts: int = Form(25)):
    run_id = influence_service.default_run_id(granularity, start, end)
    params = {"start": start, "end": end, "granularity": granularity,
              "cap_company_contacts": int(cap_company_contacts), "run_id": run_id}
    jobs.enqueue("skill", _MODULE, params=params,
                 label=f"Influence {granularity} {start}..{end}")
    return RedirectResponse("/influence", status_code=303)
