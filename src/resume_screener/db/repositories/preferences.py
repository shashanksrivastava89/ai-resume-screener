"""HR-preference repository (P49) — the DB backend for signals/preferences.jsonl.

The file store is event-sourced (one line per transition, latest-per-pref_id wins);
the DB stores the folded LATEST state as one row per `pref_id`, upserted. Everything
crosses this boundary as the SAME dict `preferences.py` folds, so callers stay
backend-agnostic and the API renders either identically. All lifecycle logic
(classification, status transitions, review-due) stays in `preferences.py`; this is
storage only.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.preference import HrPreference

# The persisted fields, in the order the file record carries them (owner_email
# surfaces as the file key "owner").
_FIELDS = ("owner_name", "text", "action", "status", "classification", "cap_points",
           "approved_by", "approved_at", "review_due", "decline_note",
           "actor", "actor_role", "ts")


def _to_dict(row: HrPreference) -> dict:
    """Shape a row like a folded preferences.jsonl record (owner, not owner_email)."""
    out = {
        "pref_id": row.pref_id,
        "owner": row.owner_email,
        "owner_name": row.owner_name,
        "text": row.text,
        "action": row.action,
        "status": row.status,
        "classification": row.classification,
        "cap_points": row.cap_points,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at,
        "review_due": row.review_due,
        "actor": row.actor,
        "actor_role": row.actor_role,
        "ts": row.ts,
    }
    if row.decline_note is not None:
        out["decline_note"] = row.decline_note
    return out


def _apply(row: HrPreference, record: dict) -> None:
    row.owner_email = record.get("owner") or ""
    row.owner_name = record.get("owner_name")
    row.text = record.get("text") or ""
    row.action = record.get("action")
    row.status = record.get("status") or ""
    row.classification = record.get("classification")
    row.cap_points = record.get("cap_points")
    row.approved_by = record.get("approved_by")
    row.approved_at = record.get("approved_at")
    row.review_due = record.get("review_due")
    row.decline_note = record.get("decline_note")
    row.actor = record.get("actor")
    row.actor_role = record.get("actor_role")
    row.ts = record.get("ts")


def upsert(session: Session, record: dict) -> dict:
    """Insert or replace the row for `record['pref_id']` (latest-wins fold)."""
    row = session.scalar(select(HrPreference).where(HrPreference.pref_id == record["pref_id"]))
    if row is None:
        row = HrPreference(pref_id=record["pref_id"])
        session.add(row)
    _apply(row, record)
    session.flush()
    return _to_dict(row)


def fold(session: Session) -> dict[str, dict]:
    """{pref_id: record} — every preference's current state (mirrors _fold())."""
    return {r.pref_id: _to_dict(r) for r in session.scalars(select(HrPreference))}


def get(session: Session, pref_id: str) -> dict | None:
    row = session.scalar(select(HrPreference).where(HrPreference.pref_id == pref_id))
    return _to_dict(row) if row else None


def import_prefs(session: Session, records: list[dict]) -> dict:
    """Backfill folded preferences. Skips if the table already holds rows."""
    if session.scalar(select(func.count()).select_from(HrPreference)):
        return {"domain": "preferences", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    added = 0
    for record in records:
        if record.get("pref_id"):
            upsert(session, record)
            added += 1
    return {"domain": "preferences", "added": added, "skipped": added == 0,
            "reason": "" if added else "no preferences"}
