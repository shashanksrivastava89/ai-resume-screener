"""Run-index repository — the DB backend for the screening-run index (Phase 4).

`screener run` still writes run_meta.json + per-candidate JSON + reports to disk;
this table is the queryable INDEX over those runs (one row per run, region +
jurisdiction + versions + counts + bands stamped). `/api/runs` lists from here, and
`service.read_run_meta` falls back to `run_meta_from_row` when the on-disk file is
gone.

Idempotent by `run_id` (the unique pipeline run id): re-recording the same run
updates its row, and the importer inserts a run only if its id is new — so both the
live write and a re-run of `screener db import` are safe.

A run's `region_id` is its GOVERNING region (derived from the job's jurisdiction),
so a region's HR Head/Legal see every run whose law they own. Rows carry `req_code`
(the role slug / output-folder name) as a soft link — the API routes on it before
the requisition table is populated (Phase 6).
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from resume_screener.db.models.runtime import Run

_VERSION_RE = re.compile(r"v(\d+)")


def _parse_version(value) -> int | None:
    """Pull the integer out of a version stamp like 'name (v3)' or 'v3 (hash)'."""
    match = _VERSION_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _region_scope(stmt, region_id: str | None):
    """region_id given → that region's runs plus global (NULL) ones; None → all."""
    if region_id is None:
        return stmt
    return stmt.where(or_(Run.region_id == region_id, Run.region_id.is_(None)))


def record_run(
    session: Session,
    meta: dict,
    *,
    req_code: str | None = None,
    region_id: str | None = None,
    finished_at: datetime | None = None,
) -> Run:
    """Insert (or update, keyed by run_id) the index row for a completed run from its
    run_meta dict. Missing fields are tolerated — the row mirrors what the run stamped."""
    run_id = str(meta.get("run_id", ""))
    versions = meta.get("versions") or {}
    cost = meta.get("cost_estimate") or {}
    fields = dict(
        req_code=req_code,
        region_id=region_id,
        role_title=meta.get("role_title"),
        jurisdiction=meta.get("jurisdiction"),
        policy_pack=meta.get("policy_pack"),
        rubric_version=_parse_version(versions.get("rubric")),
        registry_version=_parse_version(versions.get("registry")),
        versions=versions or None,
        counts=meta.get("counts") or None,
        bands=meta.get("bands") or None,
        obligations=meta.get("obligations") or None,
        cost_estimate=cost.get("estimated_usd") if isinstance(cost, dict) else None,
        status="complete",
        started_utc=meta.get("started_utc"),
        finished_at=finished_at,
    )
    row = session.scalar(select(Run).where(Run.run_id == run_id)) if run_id else None
    if row is None:
        row = Run(run_id=run_id, **fields)
        session.add(row)
    else:  # same run_id re-recorded → refresh in place
        for key, value in fields.items():
            setattr(row, key, value)
    session.flush()
    return row


# Newest-first by the run's OWN recorded time, not the row's insert time: started_utc
# is an ISO-8601 string (lexicographically chronological) and run_id embeds
# YYYYMMDD-HHMMSS, so this ordering is clock-independent and stable — Run has a uuid
# PK, so there's no monotonic id to lean on, and created_at ties under Windows' coarse
# clock. run_id desc breaks a same-second tie deterministically.
_NEWEST_FIRST = (Run.started_utc.desc(), Run.run_id.desc())


def list_runs(session: Session, *, region_id: str | None = None, limit: int | None = None) -> list[Run]:
    """All indexed runs, newest first (mirrors the file glob's mtime ordering)."""
    stmt = _region_scope(select(Run), region_id).order_by(*_NEWEST_FIRST)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def latest_run_for(session: Session, req_code: str) -> Run | None:
    """The most recent run for a role (its output folder holds only the latest run's
    artifacts, so this is the run read_run_meta reconstructs when the file is gone)."""
    return session.scalars(
        select(Run).where(Run.req_code == req_code).order_by(*_NEWEST_FIRST).limit(1)
    ).first()


def latest_runs_for(session: Session, req_codes: list[str]) -> dict[str, Run]:
    """{req_code: most recent Run} for many roles in ONE query — the dashboard asks
    for every role whose run_meta.json is missing (2026-09-10)."""
    codes = [c for c in dict.fromkeys(req_codes) if c]
    if not codes:
        return {}
    out: dict[str, Run] = {}
    for row in session.scalars(
        select(Run).where(Run.req_code.in_(codes)).order_by(*_NEWEST_FIRST)
    ):
        out.setdefault(row.req_code, row)   # ordered newest-first: first wins
    return out


def has_run(session: Session, run_id: str) -> bool:
    return session.scalar(select(Run.id).where(Run.run_id == run_id)) is not None


def run_index_item(row: Run) -> dict:
    """The raw fields the /api/runs list item is composed from (service adds display)."""
    return {
        "req_id": row.req_code,
        "role": row.role_title,
        "run_id": row.run_id,
        "jurisdiction": row.jurisdiction,
        "when": row.started_utc,
        "counts": row.counts or {},
        "bands": row.bands or {},
        "versions": row.versions or {},
    }


def run_meta_from_row(row: Run) -> dict:
    """Reconstruct a run_meta-like dict from a row — the read_run_meta FALLBACK for
    when the on-disk run_meta.json is missing. Best-effort: fields not indexed here
    (extraction stats, redaction flag, per-doc errors) are simply absent."""
    return {
        "run_id": row.run_id,
        "role_title": row.role_title,
        "jurisdiction": row.jurisdiction,
        "policy_pack": row.policy_pack,
        "started_utc": row.started_utc,
        "versions": row.versions or {},
        "counts": row.counts or {},
        "bands": row.bands or {},
        "obligations": row.obligations or [],
        "cost_estimate": {"estimated_usd": row.cost_estimate},
    }
