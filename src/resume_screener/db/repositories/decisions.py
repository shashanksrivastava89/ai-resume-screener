"""Decision repository — the DB backend for the feedback log (Phase 2).

Same guarantees as the JSONL log it mirrors (feedback.py): APPEND-ONLY (a
re-decision is a new row, never an update/delete) and a non-empty reason is
required in code. Converts between the §4 Pydantic `Decision` (the contract every
caller speaks) and the ORM row, so nothing outside this module is ORM-aware.

Ordering matches the JSONL semantics exactly: `list_decisions` returns oldest
first, and "latest wins" is decided by insertion order via the table's monotonic
autoincrement id — mirroring "later line wins" reliably even for same-instant rows.

The AI's verdict at rest (InfoSec, 2026-10-14): with DECISION_KEY_SECRET set, a new
row's `suggestion` (the AI's band + score the reviewer saw) and `reason` are sealed
into `verdict_enc` and the plain columns are left empty; reads open the envelope, so
nothing above this module knows the difference. See fieldcrypto.py.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener import fieldcrypto
from resume_screener.db.models.runtime import Decision as DecisionRow
from resume_screener.models import Decision


def _verdict_fields(row: DecisionRow) -> dict:
    """The row's suggestion + reason — opened from the envelope when sealed."""
    if row.verdict_enc:
        return fieldcrypto.open_or_placeholder(row.verdict_enc, row.run_id, row.candidate_id)
    return {"suggestion": row.suggestion or "", "reason": row.reason or ""}


def _to_contract(row: DecisionRow) -> Decision:
    fields = _verdict_fields(row)
    return Decision(
        candidate_id=row.candidate_id,
        run_id=row.run_id,
        req_id=row.req_code or "",
        suggestion=fields.get("suggestion") or "",
        human_action=row.human_action,
        final_band=row.final_band,
        reason=fields.get("reason") or "",
        reviewer=row.reviewer,
        versions=row.versions or {},
        ts=row.ts or "",
    )


def seal_row(row: DecisionRow) -> bool:
    """Seal one plain row in place (the encrypt-decisions sweep). False when it was
    already sealed. Raises fieldcrypto.DecisionKeyUnavailable without a usable key."""
    if row.verdict_enc:
        return False
    row.verdict_enc = fieldcrypto.seal(
        {"suggestion": row.suggestion or "", "reason": row.reason or ""},
        row.run_id, row.candidate_id)
    row.suggestion = None
    row.reason = ""
    return True


def reseal_row(row: DecisionRow, current: str) -> bool:
    """Re-seal a sealed row under the `current` key version (key rotation). False when
    it already uses it. Raises when the row cannot be opened: a sweep never replaces
    a verdict it could not read, and the caller's transaction rolls the batch back."""
    if not row.verdict_enc or fieldcrypto.envelope_version(row.verdict_enc) == current:
        return False
    fields = fieldcrypto.open_envelope(row.verdict_enc, row.run_id, row.candidate_id)
    row.verdict_enc = fieldcrypto.seal(fields, row.run_id, row.candidate_id)
    return True


def add_decision(
    session: Session,
    decision: Decision,
    *,
    requisition_id: str | None = None,
    region_id: str | None = None,
    reviewer_id: str | None = None,
) -> DecisionRow:
    """Append one decision. Raises ValueError on an empty reason — the same
    guarantee feedback.record_decision makes, enforced where it can't be bypassed.

    `requisition_id` / `region_id` / `reviewer_id` are the resolved FKs (region_id
    = the job's GOVERNING region); all optional so callers can migrate incrementally.
    """
    if not decision.reason.strip():
        raise ValueError("A reason is required for every decision (agree, override or re-rank).")
    # Sealed BEFORE the row exists: a configured key that cannot be read raises here,
    # so a verdict is never written in plain text by accident.
    sealed = (fieldcrypto.seal({"suggestion": decision.suggestion, "reason": decision.reason},
                               decision.run_id, decision.candidate_id)
              if fieldcrypto.enabled() else None)
    row = DecisionRow(
        run_id=decision.run_id,
        candidate_id=decision.candidate_id,
        requisition_id=requisition_id,
        region_id=region_id,
        reviewer_id=reviewer_id,
        reviewer=decision.reviewer,
        suggestion=None if sealed else decision.suggestion,
        human_action=decision.human_action,
        final_band=decision.final_band,
        reason="" if sealed else decision.reason,
        versions=decision.versions,
        ts=decision.ts,
        req_code=decision.req_id or None,
        verdict_enc=sealed,
    )
    session.add(row)
    session.flush()
    return row


def table_stamp(session: Session) -> tuple[int, int]:
    """(row count, highest id) — a cheap identity for "have the decisions changed?".

    The log is APPEND-ONLY and its primary key is an autoincrementing integer, so
    this pair is exact: an append moves both, and nothing else can move either. That
    is what lets the parsed list be cached across requests, the way the file backend
    has always cached it on the log's (size, mtime) — without it, every request that
    needs a decision read the whole table and revalidated every row (2026-09-10)."""
    row = session.execute(
        select(func.count(DecisionRow.id), func.max(DecisionRow.id))
    ).one()
    return (int(row[0] or 0), int(row[1] or 0))


def list_decisions(session: Session) -> list[Decision]:
    """All decisions, oldest first (mirrors feedback.load_decisions)."""
    rows = session.scalars(select(DecisionRow).order_by(DecisionRow.id))
    return [_to_contract(r) for r in rows]


def latest_decision_for(
    session: Session, run_id: str, candidate_id: str
) -> Decision | None:
    """The most recent decision for a candidate on a run (highest id = latest)."""
    row = session.scalars(
        select(DecisionRow)
        .where(DecisionRow.run_id == run_id, DecisionRow.candidate_id == candidate_id)
        .order_by(DecisionRow.id.desc())
        .limit(1)
    ).first()
    return _to_contract(row) if row else None


def encrypt_plain_rows(session: Session, *, rewrap: bool = False) -> tuple[int, int, int]:
    """Seal every plain row (the one-off sweep after a key is first configured) and,
    with `rewrap`, re-seal rows sealed under an older key version (the second half of
    a rotation). Returns (sealed now, re-sealed now, left as they were). Idempotent:
    run it twice and the second run changes nothing."""
    sealed = resealed = untouched = 0
    current = fieldcrypto.current_version() if rewrap else ""
    for row in session.scalars(select(DecisionRow).order_by(DecisionRow.id)):
        if seal_row(row):
            sealed += 1
        elif rewrap and reseal_row(row, current):
            resealed += 1
        else:
            untouched += 1
    session.flush()
    return sealed, resealed, untouched
