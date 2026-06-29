"""GTM Intelligence web application (FastAPI + SQLite, server-rendered).

A deterministic UI over the existing `skills/` analytics engine. It does not
reimplement any analysis — it invokes skills as subprocesses (see webapp.jobs),
persists runs/results in SQLite (see webapp.db), and renders dashboards.
"""
