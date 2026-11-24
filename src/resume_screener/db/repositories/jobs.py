"""Screening-job repository — the DB backend for live job state (Phase 5).

The API shells out to `screener run` and tracks its progress; this table is where
that progress lives when `jobs_backend=db`, replacing the in-memory dict in
api/jobs.py. One row per job (keyed by the public `job_id`, which IS the row's PK),
carrying the {status, done, total, message, error} the Screen page polls, plus the
governing region and the role slug (`req_code`) the API routes on.

Why a table: state survives a restart and is readable by any process, so the
subprocess model can become a real queue + workers (§12) behind the same interface
without the UI changing. This repo only stores/reads state — it never spawns the
subprocess (that stays in api/jobs.py).

`reap_running` flips jobs a previous process left "running" (its thread + subprocess
died with it) to an error state, so a restart never leaves a phantom job blocking
new screens. A run is cache-resumable, so the recovery is simply to screen again.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from resume_screener.db.models.runtime import ScreeningJob

# Shown for a job orphaned by a restart (see reap_running). Plain recruiter language,
# and actionable: re-screening resumes from the content-hash cache, so it's cheap.
INTERRUPTED_MESSAGE = "Screening was interrupted by a restart — screen again to pick up where it left off."


def _by_job_id(session: Session, job_id: str) -> ScreeningJob | None:
    return session.get(ScreeningJob, job_id)


def create_job(
    session: Session, job_id: str, req_code: str, *, region_id: str | None = None
) -> ScreeningJob:
    """Insert a freshly-started job (status=running). `job_id` is the public id the
    UI polls and the row PK; `req_code` is the role slug the Screen page routes on;
    `region_id` is the job's GOVERNING region (resolved from its jurisdiction)."""
    row = ScreeningJob(
        id=job_id, req_code=req_code, region_id=region_id,
        status="running", done=0, total=0, message="Starting…", error=None,
    )
    session.add(row)
    session.flush()
    return row


def get_job(session: Session, job_id: str) -> ScreeningJob | None:
    return _by_job_id(session, job_id)


def active_job_for(session: Session, req_code: str) -> ScreeningJob | None:
    """The running job for a role, if any (newest first). Used to dedupe: the Screen
    page won't start a second run for a role that's already screening."""
    return session.scalars(
        select(ScreeningJob)
        .where(ScreeningJob.req_code == req_code, ScreeningJob.status == "running")
        .order_by(ScreeningJob.created_at.desc(), ScreeningJob.id.desc())
        .limit(1)
    ).first()


def latest_job_for(session: Session, req_code: str) -> ScreeningJob | None:
    """The most recent job for a role regardless of status (newest first). Lets
    the Candidates tab re-show a just-stopped / failed run after a reload or a
    user switch — the running-only `active_job_for` returns nothing once it ends."""
    return session.scalars(
        select(ScreeningJob)
        .where(ScreeningJob.req_code == req_code)
        .order_by(ScreeningJob.created_at.desc(), ScreeningJob.id.desc())
        .limit(1)
    ).first()


def latest_per_req(session: Session) -> dict[str, ScreeningJob]:
    """{req_code: its most recent job, whatever the status} in ONE query.

    `screen_lock` needs this to answer "is the newest job paused?" — asking per role
    is a query per dashboard row (2026-09-10)."""
    out: dict[str, ScreeningJob] = {}
    for row in session.scalars(
        select(ScreeningJob).order_by(ScreeningJob.created_at.desc(), ScreeningJob.id.desc())
    ):
        if row.req_code:
            out.setdefault(row.req_code, row)   # newest-first: the first wins
    return out


def all_active(session: Session) -> list[ScreeningJob]:
    """Every running job across all requisitions (newest first). Powers the
    Requisitions list's live "Screening N/M" column — one poll for the whole
    dashboard rather than one per row."""
    return list(session.scalars(
        select(ScreeningJob)
        .where(ScreeningJob.status == "running")
        .order_by(ScreeningJob.created_at.desc(), ScreeningJob.id.desc())
    ))


def save_state(session: Session, job_id: str, state: dict) -> ScreeningJob | None:
    """Persist the current public state of a running/finished job. `state` carries
    any of status/done/total/message/error (the api/jobs runner sends a full
    snapshot on each progress tick and on the terminal state). No-op if the row is
    gone (e.g. reaped)."""
    row = _by_job_id(session, job_id)
    if row is None:
        return None
    if "status" in state:
        row.status = state["status"]
    if "done" in state:
        row.done = int(state["done"] or 0)
    if "total" in state:
        row.total = int(state["total"] or 0)
    if "message" in state:
        row.message = state["message"]
    if "error" in state:
        row.error = state["error"]
    session.flush()
    return row


def reap_running(session: Session) -> int:
    """Mark every job still 'running' as interrupted — called once at API startup.

    In this single-process model a running job's thread + subprocess die with the
    process, so any 'running' row found at startup is stale. §12's real workers
    replace this with lease/heartbeat logic (a live worker's jobs must not be reaped
    by a peer's restart). Returns how many rows were reaped.
    """
    rows = list(session.scalars(
        select(ScreeningJob).where(ScreeningJob.status == "running")
    ))
    for row in rows:
        row.status = "error"
        row.message = INTERRUPTED_MESSAGE
        row.error = INTERRUPTED_MESSAGE
    session.flush()
    return len(rows)


def job_public(row: ScreeningJob) -> dict:
    """The public shape the /api/screen routes return — identical to the in-memory
    backend's Job.public() (job_id = the row PK; req_id = the role slug)."""
    return {
        "job_id": row.id,
        "req_id": row.req_code,
        "status": row.status,
        "done": row.done or 0,
        "total": row.total or 0,
        "message": row.message or "",
        "error": row.error or "",
    }


def delete_for_req(session: Session, req_code: str) -> int:
    """Drop every job row for a requisition; returns how many. Used when a
    requisition is DELETED — a job row that outlives its requisition makes a
    recreated one with the same id look like it has a run in flight."""
    from sqlalchemy import delete

    result = session.execute(delete(ScreeningJob).where(ScreeningJob.req_code == req_code))
    return int(result.rowcount or 0)
