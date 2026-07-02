"""FastAPI entrypoint. Run locally with:  uvicorn webapp.main:app --reload"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from . import auth, db, jobs
from .config import SESSION_SECRET
from .routers import audit, dashboard, data, influence, mmm
from .templating import render

app = FastAPI(title="GTM Intelligence")

# Order matters: SessionMiddleware must wrap AuthMiddleware so sessions are
# available when auth checks run.
app.add_middleware(auth.AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=14 * 24 * 3600)

_STATIC = Path(__file__).resolve().parent / "static"
_STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

app.include_router(dashboard.router)
app.include_router(mmm.router)
app.include_router(influence.router)
app.include_router(data.router)
app.include_router(audit.router)


@app.on_event("startup")
def _startup():
    db.init_db()
    jobs.start_workers()


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "auth": auth.auth_enabled()})


@app.get("/login")
def login_form(request: Request, error: str = ""):
    if auth.is_authed(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", error=error, enabled=auth.auth_enabled())


@app.post("/login")
def login_submit(request: Request, password: str = Form("")):
    if not auth.auth_enabled() or auth.check_password(password):
        auth.login_session(request)
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    auth.logout_session(request)
    return RedirectResponse("/login", status_code=303)
