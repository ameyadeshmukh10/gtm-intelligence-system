"""Single shared-password gate.

If APP_PASSWORD is set, every request (outside the allowlist) must have an
authenticated session, otherwise it is redirected to /login. If APP_PASSWORD is
empty, auth is disabled — intended for local development.

DEFERRED (later phase): real multi-user accounts. To add them, introduce a
`user` table, replace `check_password` with a per-user credential check, and
store `user_id` (not just `authed`) in the session. Everything auth-related is
intentionally confined to this module so that swap is localized.
"""
from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse

from .config import APP_PASSWORD

# Paths reachable without a session.
_ALLOW_PREFIXES = ("/login", "/logout", "/static", "/health")


def auth_enabled() -> bool:
    return bool(APP_PASSWORD)


def check_password(candidate: str) -> bool:
    return bool(APP_PASSWORD) and hmac.compare_digest(candidate or "", APP_PASSWORD)


def is_authed(request: Request) -> bool:
    if not auth_enabled():
        return True
    return bool(request.session.get("authed"))


def login_session(request: Request) -> None:
    request.session["authed"] = True


def logout_session(request: Request) -> None:
    request.session.clear()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not auth_enabled() or any(path.startswith(p) for p in _ALLOW_PREFIXES):
            return await call_next(request)
        if request.session.get("authed"):
            return await call_next(request)
        # Unauthenticated. HTMX/XHR requests get a 401 with a redirect header;
        # full-page navigations get a redirect to the login screen.
        if request.headers.get("hx-request") or "application/json" in request.headers.get("accept", ""):
            resp = JSONResponse({"detail": "auth required"}, status_code=401)
            resp.headers["HX-Redirect"] = "/login"
            return resp
        return RedirectResponse("/login", status_code=303)
