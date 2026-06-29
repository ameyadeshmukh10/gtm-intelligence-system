"""GTM audit browser — list and render the analytical skills' result JSONs."""
from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.requests import Request

from ..services import results_service
from ..templating import render

router = APIRouter()


@router.get("/audit")
def audit_index(request: Request):
    results = results_service.list_results()
    return render(request, "audit.html", results=results, view=None)


@router.get("/audit/view")
def audit_view(request: Request, skill: str = Query(...),
               run_id: str = Query(None), path: str = Query(None)):
    res = results_service.load_result(path, run_id, skill)
    view = results_service.render(skill, res) if res else None
    results = results_service.list_results()
    return render(request, "audit.html", results=results, view=view,
                  selected={"skill": skill, "run_id": run_id, "path": path})
