"""Marketing Mix Model dashboard + budget what-if planner."""
from __future__ import annotations

import json

from fastapi import APIRouter, Form
from starlette.requests import Request

from .. import db
from ..config import CANONICAL_MMM_RUN_ID
from ..services import mmm_service
from ..templating import render

router = APIRouter()


def _channel_ci_includes_zero(ch) -> bool:
    ci = ch.get("contribution_ci90", [0, 0])
    return ci[0] is not None and ci[0] < 0.5


@router.get("/mmm")
def mmm_dashboard(request: Request):
    res = mmm_service.load_result()
    if not res:
        return render(request, "mmm.html", res=None, ts=None,
                      charts=None, channels=None)
    ts = mmm_service.load_timeseries()
    channels = res.get("channels", [])
    rank = {c: i for i, c in enumerate(res.get("reallocation_ranking", []))}
    for c in channels:
        c["ci_zero"] = _channel_ci_includes_zero(c)
        c["rank"] = rank.get(c["channel"], 99)

    # Chart payloads (consumed by Plotly in the template).
    contrib_chart = {
        "channels": [c["channel"] for c in channels],
        "means": [c.get("contribution_mean", 0) for c in channels],
        "ci_lo": [c.get("contribution_ci90", [0, 0])[0] for c in channels],
        "ci_hi": [c.get("contribution_ci90", [0, 0])[1] for c in channels],
    }
    curves = [{"channel": c["channel"],
               "spend": [p["spend"] for p in c.get("response_curve", [])],
               "contribution": [p["contribution"] for p in c.get("response_curve", [])],
               "current_spend": c.get("total_spend", 0),
               "current_contribution": c.get("contribution_mean", 0)}
              for c in channels]
    charts = {"contrib": contrib_chart, "curves": curves, "timeseries": ts,
              "baseline": res.get("baseline"), "incremental": res.get("incremental")}
    return render(request, "mmm.html", res=res, channels=channels, charts=charts)


@router.get("/mmm/whatif")
def whatif_page(request: Request):
    res = mmm_service.load_result()
    saved = db.query("SELECT * FROM scenario WHERE mmm_run_id=? ORDER BY id DESC LIMIT 20",
                     (CANONICAL_MMM_RUN_ID,))
    return render(request, "whatif.html", res=res, saved=saved, projection=None)


@router.post("/mmm/whatif")
async def whatif_run(request: Request):
    form = await request.form()
    res = mmm_service.load_result()
    plan = {}
    for ch in (res or {}).get("channels", []):
        raw = form.get(f"spend_{ch['channel']}")
        if raw not in (None, ""):
            try:
                plan[ch["channel"]] = float(raw)
            except ValueError:
                pass
    projection = mmm_service.whatif(plan)
    name = (form.get("scenario_name") or "").strip()
    if name and form.get("save"):
        db.execute("INSERT INTO scenario (mmm_run_id, name, spend_plan_json, projected_json) "
                   "VALUES (?,?,?,?)",
                   (CANONICAL_MMM_RUN_ID, name, json.dumps(plan), json.dumps(projection)))
    saved = db.query("SELECT * FROM scenario WHERE mmm_run_id=? ORDER BY id DESC LIMIT 20",
                     (CANONICAL_MMM_RUN_ID,))
    return render(request, "whatif.html", res=res, saved=saved, projection=projection)
