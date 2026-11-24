"""App-event repository (P49) — the DB backend for signals/events.jsonl.

Append-only, ordered by the monotonic `id` (never the coarse `ts`), so the
explorer's oldest-first merge and "what came after" hold exactly as they do on the
file spine. Rows cross the boundary as the same dict `events.emit_event` builds.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.event import AppEvent


def _to_dict(row: AppEvent) -> dict:
    return {
        "ts": row.ts, "kind": row.kind, "actor": row.actor,
        "actor_role": row.actor_role, "req_id": row.req_id,
        "candidate_id": row.candidate_id, "summary": row.summary,
        "data": row.data or {},
    }


def append(session: Session, entry: dict) -> dict:
    """Append one event; returns it as written (mirrors emit_event's return)."""
    row = AppEvent(
        ts=entry.get("ts"), kind=entry.get("kind") or "", actor=entry.get("actor"),
        actor_role=entry.get("actor_role"), req_id=entry.get("req_id"),
        candidate_id=entry.get("candidate_id"), summary=entry.get("summary") or "",
        data=entry.get("data") or {},
    )
    session.add(row)
    session.flush()
    return entry


def load(session: Session, req_id: str | None = None,
         kinds: Iterable[str] | None = None) -> list[dict]:
    """Events oldest-first (by id), optionally filtered to one req and/or kinds."""
    stmt = select(AppEvent).order_by(AppEvent.id)
    if req_id is not None:
        stmt = stmt.where(AppEvent.req_id == req_id)
    if kinds is not None:
        stmt = stmt.where(AppEvent.kind.in_(list(kinds)))
    return [_to_dict(r) for r in session.scalars(stmt)]


def actors_by_req(session: Session) -> dict[str, set[str]]:
    """req_id -> the (lower-cased) actors of its events: one DISTINCT query."""
    out: dict[str, set[str]] = {}
    stmt = (select(AppEvent.req_id, AppEvent.actor)
            .where(AppEvent.req_id.is_not(None), AppEvent.actor.is_not(None)).distinct())
    for req_id, actor in session.execute(stmt):
        if req_id and actor:
            out.setdefault(req_id, set()).add(str(actor).lower())
    return out


def import_events(session: Session, entries: list[dict]) -> dict:
    """Backfill the event spine, preserving file order (→ id order). Skips if the
    table already holds rows."""
    if session.scalar(select(func.count()).select_from(AppEvent)):
        return {"domain": "events", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    added = 0
    for entry in entries:
        append(session, entry)
        added += 1
    return {"domain": "events", "added": added, "skipped": added == 0,
            "reason": "" if added else "no events"}
