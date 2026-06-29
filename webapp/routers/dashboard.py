"""Overview page — system status + quick links."""
from __future__ import annotations

from fastapi import APIRouter
from starlette.requests import Request

from .. import db
from ..config import CANONICAL_MMM_RUN_ID
from ..services import mmm_service
from ..templating import render

router = APIRouter()


@router.get("/")
def overview(request: Request):
    mmm = mmm_service.load_result(CANONICAL_MMM_RUN_ID)
    datasets = db.query("SELECT entity, source, n_records, pulled_at FROM dataset "
                        "ORDER BY id DESC LIMIT 8")
    recent_jobs = db.query("SELECT id, name, kind, status, created_at FROM job "
                           "ORDER BY id DESC LIMIT 8")
    n_results = db.query_one("SELECT COUNT(*) c FROM result_artifact")["c"]
    return render(request, "dashboard.html", mmm=mmm, datasets=datasets,
                  recent_jobs=recent_jobs, n_results=n_results)
