"""Governance repository — the DB backend for the audit log + review sign-off (Phase 3).

Same guarantees as the JSONL files it mirrors (governance.py): the audit trail is
APPEND-ONLY (never updated or deleted), and the "pending HR + Legal review" banner
is decided by INSERTION ORDER, not wall-clock timestamps — the OS clock's coarse
resolution can stamp a sign-off and an immediately-following publish in the same
second, so "came after" must mean "later row", which the monotonic autoincrement
`AuditEntry.id` gives us unambiguously.

Region scope (the Phase-3 addition): every audit entry and review sign-off carries
a `region_id` so each region has its own trail and its own banner. A `region_id`
of NULL means "all regions" (a global change, e.g. the shared signal registry).
Callers query a region with `region_id=<code's row id>`: they see that region's own
entries PLUS the global ones. `region_id=None` on a query means the whole-org view
(every entry) — which is exactly today's single global log, so the file→DB switch
is behaviour-preserving for the current single-region pilot.

Everything crosses this boundary as plain dicts shaped like the JSONL lines
(`ts/action/target/version/author/summary/rationale/region_id`), so nothing outside
this module is ORM-aware and the API renders DB and file entries identically.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from resume_screener.db.models.runtime import AuditEntry, ReviewSignoff

# Publishing a rule/pack/scoring-model change is what reopens the review banner
# (mirrors the file logic's marker set exactly). P51: a publish that went through
# a second-leader approval (approved_by set) does NOT reopen it — the dual review
# happened before the change took effect.
_PUBLISH_ACTIONS = ("publish_registry", "publish_pack", "publish_scoring_model")


def _region_filter(stmt: Select, region_id: str | None) -> Select:
    """Scope a query to a region: its OWN entries plus the global (NULL) ones.

    `region_id=None` means the whole-org view — no filter, every row — which is
    identical to today's single global log.
    """
    if region_id is None:
        return stmt
    return stmt.where(
        or_(AuditEntry.region_id == region_id, AuditEntry.region_id.is_(None))
    )


def _audit_to_dict(row: AuditEntry) -> dict:
    """Shape an AuditEntry row like a JSONL audit line (author, not actor)."""
    out: dict = {
        "ts": row.ts or "",
        "action": row.action,
        "author": row.actor,
        "target": row.target,
        "summary": row.summary,
        "rationale": row.rationale,
        "region_id": row.region_id,
    }
    if row.version is not None:
        out["version"] = row.version
    if row.approved_by:
        out["approved_by"] = row.approved_by
    if row.change_id:
        out["change_id"] = row.change_id
    return out


# --- audit log --------------------------------------------------------------------------


def append_audit(
    session: Session, entry: dict, *, region_id: str | None = None
) -> AuditEntry:
    """Append one audit entry (append-only — never updated/deleted).

    `entry` is the same dict governance.append_audit builds for the file log
    (`action/target/version/author/rationale/summary`, optional `ts`). `region_id`
    is the region whose config was touched (NULL = a global change).
    """
    row = AuditEntry(
        region_id=region_id,
        actor=entry.get("author"),
        action=entry["action"],
        target_type=entry.get("target_type"),
        target=entry.get("target"),
        version=entry.get("version"),
        summary=entry.get("summary"),
        rationale=entry.get("rationale"),
        ts=entry.get("ts"),
        approved_by=entry.get("approved_by"),
        change_id=entry.get("change_id"),
    )
    session.add(row)
    session.flush()
    return row


def load_audit(session: Session, *, region_id: str | None = None) -> list[dict]:
    """All audit entries, oldest first (mirrors governance.load_audit ordering)."""
    stmt = _region_filter(select(AuditEntry), region_id).order_by(AuditEntry.id)
    return [_audit_to_dict(r) for r in session.scalars(stmt)]


# --- review sign-off + the pending banner -----------------------------------------------


def record_review(
    session: Session,
    reviewers: str,
    review_date: str,
    recorded_by: str,
    *,
    region_id: str | None = None,
    ts: str | None = None,
) -> ReviewSignoff:
    """Record an HR + Legal review for a region (NULL = all regions).

    Writes BOTH a structured ReviewSignoff row (what `last_review` reads) and an
    audit entry marker (what `review_pending` orders against) — the same two writes
    the file backend makes. Raises ValueError on empty reviewers/date, the guarantee
    governance.record_review makes, enforced where it can't be bypassed.
    """
    if not reviewers.strip() or not review_date.strip():
        raise ValueError("Both the reviewers and the review date are required.")
    row = ReviewSignoff(
        region_id=region_id,
        reviewers=reviewers.strip(),
        review_date=review_date.strip(),
        recorded_by=recorded_by,
        ts=ts,
    )
    session.add(row)
    append_audit(
        session,
        {
            "action": "review_signoff",
            "target": "configuration",
            "author": recorded_by,
            "rationale": f"HR + Legal review recorded ({review_date.strip()})",
            "summary": f"reviewers: {reviewers.strip()}",
            "ts": ts,
        },
        region_id=region_id,
    )
    session.flush()
    return row


def last_review(session: Session, *, region_id: str | None = None) -> dict | None:
    """The most recent review sign-off for a region (its own or a global one).

    Returns the same keys the file `review_status.json` carries — {ts, reviewers,
    date, by} — so `/api/me` renders identically on either backend.
    """
    # ReviewSignoff has a uuid PK (not monotonic), so ordering is by the tz-aware
    # created_at (microsecond precision — evaluated per row at insert).
    stmt = select(ReviewSignoff).order_by(ReviewSignoff.created_at.desc())
    if region_id is not None:
        stmt = stmt.where(
            or_(ReviewSignoff.region_id == region_id, ReviewSignoff.region_id.is_(None))
        )
    row = session.scalars(stmt.limit(1)).first()
    if row is None:
        return None
    return {"ts": row.ts, "reviewers": row.reviewers, "date": row.review_date,
            "by": row.recorded_by}


def review_pending(session: Session, *, region_id: str | None = None) -> bool:
    """True until the CURRENT configuration is signed off for this region — i.e.
    no review yet, or a rules/policy change was published after the last sign-off.

    Decided by ROW ORDER (autoincrement id), never timestamps: same reason as the
    file version — a sign-off and a following publish can share a clock second.
    """
    signoff_stmt = _region_filter(
        select(func.max(AuditEntry.id)).where(AuditEntry.action == "review_signoff"),
        region_id,
    )
    last_signoff = session.scalar(signoff_stmt)
    if last_signoff is None:
        return True  # never reviewed → pending (internal-evaluation-only default)
    later_publish_stmt = _region_filter(
        select(AuditEntry.id).where(
            AuditEntry.action.in_(_PUBLISH_ACTIONS),
            AuditEntry.id > last_signoff,
            AuditEntry.approved_by.is_(None),  # P51: dual-approved publishes don't reopen
        ),
        region_id,
    ).limit(1)
    return session.scalar(later_publish_stmt) is not None
