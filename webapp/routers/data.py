"""Data refresh + run history: trigger pipelines/skills as background jobs,
poll job status, browse datasets and run history, upload a budget workbook.
"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File
from starlette.requests import Request
from starlette.responses import RedirectResponse

from .. import db, jobs
from ..config import RAW_DIR, BUDGET_WORKBOOK_PATH
from ..skills_registry import PIPELINES
from ..templating import render

router = APIRouter()


@router.get("/data")
def data_page(request: Request):
    datasets = db.query("SELECT * FROM dataset ORDER BY id DESC LIMIT 30")
    jobs_rows = db.query("SELECT id, name, kind, label, status, error, created_at, "
                         "finished_at FROM job ORDER BY id DESC LIMIT 30")
    pipelines = [{"key": p.key, "label": p.label, "description": p.description}
                 for p in PIPELINES.values()]
    return render(request, "data.html", datasets=datasets, jobs=jobs_rows,
                  pipelines=pipelines, workbook=BUDGET_WORKBOOK_PATH)


@router.post("/data/run/{pipeline_key}")
def run_pipeline(request: Request, pipeline_key: str):
    pipe = PIPELINES.get(pipeline_key)
    if pipe:
        jobs.enqueue("pipeline", pipeline_key, params={}, label=pipe.label)
    return RedirectResponse("/data", status_code=303)


@router.post("/data/upload-workbook")
async def upload_workbook(request: Request, file: UploadFile = File(...)):
    dest = RAW_DIR / "uploaded_workbook.numbers"
    dest.write_bytes(await file.read())
    db.execute("INSERT INTO spend_upload (filename, parsed_csv_path) VALUES (?,?)",
               (file.filename, None))
    # Kick off a rebuild using the uploaded workbook.
    jobs.enqueue("pipeline", "rebuild_mmm", params={"workbook": str(dest)},
                 label=f"Rebuild MMM from {file.filename}")
    return RedirectResponse("/data", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: int):
    """HTMX partial: live status row for a job (polled)."""
    job = db.query_one("SELECT * FROM job WHERE id=?", (job_id,))
    return render(request, "partials/job_row.html", job=job)


@router.get("/jobs/{job_id}/logs")
def job_logs(request: Request, job_id: int):
    job = db.query_one("SELECT * FROM job WHERE id=?", (job_id,))
    return render(request, "partials/job_logs.html", job=job)
