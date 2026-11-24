"""Rubric repository — the DB backend for versioned screening checklists (Phase 6c).

Stores each rubric version as a `rubric` row + ordered `requirement` children
(hard gates are kind='hard_gate' rows — one list, P33), keyed by `req_code`
(the JD stem the API routes on). Converts to/from the
§4 `Rubric` so the rubric agent, scoring and the UI stay ORM-unaware. Old versions
are kept (findings + decisions reference the version they were made with — the audit
chain), exactly like the versioned YAML files.

`requisition_id` is resolved opportunistically: NULL now (Phase 6c, no requisitions
in the DB yet), and set automatically once a requisition with the same req_code
exists (Phase 6d backfills the rest).
"""

from __future__ import annotations

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, selectinload

from resume_screener.db.models.rubric import Requirement as RequirementRow
from resume_screener.db.models.rubric import Rubric as RubricRow
from resume_screener.models import Requirement, Rubric


def _rubric_contract(row: RubricRow) -> Rubric:
    """Build the §4 Rubric from a row + children (jd_ref is set by the caller, which
    holds the path). Children are ordered by `ordinal` to preserve display order.
    Normalized on the way out, matching load_rubric's guarantee."""
    reqs = sorted(row.requirements, key=lambda r: r.ordinal)
    return Rubric(
        role_title=row.role_title or "",
        jd_ref="",  # caller (rubric.py) overrides with the JD path it was asked for
        version=row.version,
        requirements=[
            Requirement(id=r.handle or f"r{i + 1:02d}", text=r.text, type=r.type,
                        kind=r.kind, weight=r.weight,
                        critic_level="warn" if r.critic_level == "warn" else None,
                        critic_reason=r.critic_reason)
            for i, r in enumerate(reqs)
        ],
    ).normalized()


def has_any(session: Session) -> bool:
    return bool(session.scalar(select(func.count()).select_from(RubricRow)))


def latest_version(session: Session, req_code: str) -> int:
    """The highest stored version for a role, or 0 if none (so next = 1)."""
    return int(session.scalar(
        select(func.max(RubricRow.version)).where(RubricRow.req_code == req_code)
    ) or 0)


def _latest_row(session: Session, req_code: str) -> RubricRow | None:
    return session.scalars(
        select(RubricRow).where(RubricRow.req_code == req_code)
        .order_by(RubricRow.version.desc()).limit(1)
    ).first()


def latest_rubric(session: Session, req_code: str) -> Rubric | None:
    row = _latest_row(session, req_code)
    return _rubric_contract(row) if row is not None else None


def latest_rubrics(session: Session, req_codes: list[str]) -> dict[str, Rubric]:
    """{req_code: latest Rubric} for many roles in TWO queries.

    One per role cost three (the max-version probe, the row, and the lazy load of its
    requirements), so the dashboard spent 75 queries on 25 roles and would spend
    1,500 on 500 (2026-09-10). `selectinload` collapses the children into the second
    query instead of one per rubric."""
    codes = [c for c in dict.fromkeys(req_codes) if c]
    if not codes:
        return {}
    latest = session.execute(
        select(RubricRow.req_code, func.max(RubricRow.version))
        .where(RubricRow.req_code.in_(codes))
        .group_by(RubricRow.req_code)
    ).all()
    if not latest:
        return {}
    rows = session.scalars(
        select(RubricRow)
        .options(selectinload(RubricRow.requirements))
        .where(tuple_(RubricRow.req_code, RubricRow.version).in_(
            [(code, version) for code, version in latest]))
    )
    return {row.req_code: _rubric_contract(row) for row in rows}


def list_versions(session: Session, req_code: str) -> list[dict]:
    """Every stored version for a role, ascending: {version, created_at, approved_by,
    requirement_count, gate_count, must_count}. The file backend answers the same
    question by globbing `*.rubric.vN.yaml`, so the checklist history reads the same
    either way (2026-09-07: it only knew about files, so under RUBRIC_BACKEND=db the
    history came back empty)."""
    rows = session.scalars(
        select(RubricRow).where(RubricRow.req_code == req_code)
        .order_by(RubricRow.version.asc())
    ).all()
    out: list[dict] = []
    for row in rows:
        reqs = list(row.requirements)
        out.append({
            "version": row.version,
            "created_at": row.created_at,
            "approved_by": row.approved_by,
            "requirement_count": len(reqs),
            "gate_count": sum(1 for r in reqs if r.kind == "hard_gate"),
            "must_count": sum(1 for r in reqs if r.kind == "must_have"),
        })
    return out


def rubric_version(session: Session, req_code: str, version: int) -> Rubric | None:
    """One stored version as the §4 Rubric, or None when it doesn't exist."""
    row = session.scalars(
        select(RubricRow)
        .where(RubricRow.req_code == req_code, RubricRow.version == version)
    ).first()
    return _rubric_contract(row) if row is not None else None


def _requisition_id_for(session: Session, req_code: str) -> str | None:
    """The requisition row id for a req_code, if that table is populated yet (Phase
    6d). None now — the rubric carries only the soft req_code link until then."""
    from resume_screener.db.models.requisition import Requisition as RequisitionRow

    return session.scalar(select(RequisitionRow.id).where(RequisitionRow.req_code == req_code))


def save_rubric(
    session: Session,
    req_code: str,
    rubric: Rubric,
    *,
    version: int,
    approved_by: str | None = None,
) -> RubricRow:
    """Insert one rubric version (row + children). `rubric` is expected already
    normalized + stamped with `version` by the caller (rubric.save_rubric)."""
    row = RubricRow(
        req_code=req_code,
        requisition_id=_requisition_id_for(session, req_code),
        version=version,
        role_title=rubric.role_title,
        status="approved",
        approved_by=approved_by,
    )
    session.add(row)
    session.flush()  # assign row.id before children
    for i, req in enumerate(rubric.requirements):
        session.add(RequirementRow(
            rubric_id=row.id, handle=req.id, text=req.text, type=req.type,
            kind=req.kind, weight=req.weight, ordinal=i,
            critic_level=req.critic_level, critic_reason=req.critic_reason,
        ))
    session.flush()
    return row


def import_rubric_version(session: Session, req_code: str, rubric: Rubric) -> None:
    """Backfill one historical rubric version (keeps its original version number)."""
    save_rubric(session, req_code, rubric, version=rubric.version)
