"""HTTP API layer (production seed, §12).

A thin FastAPI surface over the existing engine — it does NOT re-implement any
scoring logic. Reads (roles, rubrics, review results, signals, policies, RBAC,
runs) are file I/O over the same artifacts the CLI writes; the one heavy action
that runs in-process is drafting a rubric (one model call). Screening reuses the
exact, tested `screener run` pipeline via a subprocess so the compliance-critical
path has a single source of truth.

The React app in web/ is the only client today; every route returns plain JSON.
"""
