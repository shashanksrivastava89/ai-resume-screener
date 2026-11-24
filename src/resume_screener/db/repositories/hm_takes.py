"""Hiring-manager takes repository (P49) — the DB backend for hm_takes.jsonl.

Latest-wins per (run_id, candidate_id, hm_email) — a re-take upserts the SAME row
(the file store supersedes the previous line; here we replace in place). Rows carry
soft handles (req_code, hm_email) plus the ideal FKs, set when those tables are
populated. Crosses the boundary as the same dict hm_takes.py writes (req_code
surfaces as the file key `req_id`).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.collaboration import HmTake


def _to_dict(row: HmTake) -> dict:
    return {
        "take_id": row.take_id, "req_id": row.req_code, "run_id": row.run_id,
        "candidate_id": row.candidate_id, "take": row.take, "note": row.note or "",
        "hm_email": row.hm_email, "hm_name": row.hm_name or "", "ts": row.ts,
    }


def upsert_take(session: Session, record: dict) -> dict:
    """Insert or replace the take for (run_id, candidate_id, hm_email)."""
    row = session.scalar(select(HmTake).where(
        HmTake.run_id == record.get("run_id"),
        HmTake.candidate_id == record.get("candidate_id"),
        HmTake.hm_email == record.get("hm_email"),
    ))
    if row is None:
        row = HmTake(candidate_id=record.get("candidate_id") or "")
        session.add(row)
    row.take_id = record.get("take_id") or row.take_id
    row.req_code = record.get("req_id")
    row.run_id = record.get("run_id")
    row.candidate_id = record.get("candidate_id") or ""
    row.take = record.get("take") or ""
    row.note = record.get("note") or ""
    row.hm_email = record.get("hm_email")
    row.hm_name = record.get("hm_name") or ""
    row.ts = record.get("ts")
    session.flush()
    return _to_dict(row)


def list_takes(session: Session, req_id: str | None = None,
               run_id: str | None = None) -> list[dict]:
    """Folded takes (one row per key), optionally filtered by req/run."""
    stmt = select(HmTake)
    if req_id is not None:
        stmt = stmt.where(HmTake.req_code == req_id)
    if run_id is not None:
        stmt = stmt.where(HmTake.run_id == run_id)
    return [_to_dict(r) for r in session.scalars(stmt)]


def import_takes(session: Session, records: list[dict]) -> dict:
    """Backfill folded takes. Skips if the table already holds rows."""
    if session.scalar(select(func.count()).select_from(HmTake)):
        return {"domain": "hm_takes", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    added = 0
    for record in records:
        if record.get("candidate_id"):
            upsert_take(session, record)
            added += 1
    return {"domain": "hm_takes", "added": added, "skipped": added == 0,
            "reason": "" if added else "no takes"}
