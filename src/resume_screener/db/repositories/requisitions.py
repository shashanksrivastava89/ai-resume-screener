"""Requisition / JD-document repository — the DB backend for role identity (Phase 6d).

Uniquely identifiable JDs (`jd_document.content_hash` UNIQUE) and roles
(`requisition.req_code` UNIQUE). The ATS's fetch_requisitions reads these rows when
requisitions_backend=db; résumé files + per-role batches stay on disk, so
fetch_candidates/fetch_documents are unchanged. Converts to the §4 `Requisition`
(req_id=req_code, jd_ref=jd_document.source_ref) so nothing outside is ORM-aware.

`backfill_requisition_links` turns the soft `req_code` links on rubric/run/
screening_job rows into real requisition_id FKs once the requisitions exist — the
step that completes the identity model the earlier sub-phases deferred.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.requisition import JdDocument
from resume_screener.db.models.requisition import Requisition as RequisitionRow
from resume_screener.db.models.requisition import RequisitionContact
from resume_screener.db.models.rubric import Rubric as RubricRow
from resume_screener.db.models.runtime import Run as RunRow
from resume_screener.db.models.runtime import ScreeningJob as ScreeningJobRow
from resume_screener.models import Requisition


def _requisition_contract(req: RequisitionRow, jd: JdDocument | None) -> Requisition:
    return Requisition(
        req_id=req.req_code,
        role_title=req.role_title,
        jd_ref=(jd.source_ref if jd else None) or "",
        job_location=req.job_location or "IN",
    )


def has_any(session: Session) -> bool:
    return bool(session.scalar(select(func.count()).select_from(RequisitionRow)))


def list_requisitions(session: Session) -> list[Requisition]:
    """All roles as §4 Requisitions, ordered by req_code (mirrors the file glob's
    sorted order)."""
    rows = list(session.scalars(select(RequisitionRow).order_by(RequisitionRow.req_code)))
    # ONE query for the JDs, not one per requisition (2026-09-10). This was an N+1
    # hiding in plain sight: every per-requisition route resolves its req through
    # this list, so a 25-role fixture spent 26 queries here on EVERY request — a
    # workspace poll included — and 500 roles would spend 501.
    jd_ids = {r.jd_document_id for r in rows if r.jd_document_id}
    jds = {}
    if jd_ids:
        jds = {j.id: j for j in session.scalars(
            select(JdDocument).where(JdDocument.id.in_(jd_ids)))}
    return [_requisition_contract(r, jds.get(r.jd_document_id)) for r in rows]


def table_stamp(session: Session) -> tuple[int, str]:
    """(row count, latest update) — a cheap identity for "have the requisitions
    changed?", so the hydrated list can be cached across requests without reading
    every row (2026-09-10).

    NOT max(id): the primary key is a random UUID, so a new row is very often not the
    maximum and an insert would go unnoticed. Count catches inserts and deletes,
    updated_at catches edits; the caller pairs both with the jds/ directory mtime for
    the sidecar fields this list overlays, and a 15s TTL covers the rest."""
    row = session.execute(
        select(func.count(RequisitionRow.id), func.max(RequisitionRow.updated_at))
    ).one()
    return (int(row[0] or 0), str(row[1] or ""))


def get_requisition(session: Session, req_code: str) -> Requisition | None:
    req = session.scalar(select(RequisitionRow).where(RequisitionRow.req_code == req_code))
    if req is None:
        return None
    jd = session.get(JdDocument, req.jd_document_id) if req.jd_document_id else None
    return _requisition_contract(req, jd)


def upsert_jd_document(
    session: Session, *, content_hash: str, title: str | None, source_ref: str | None,
    media_type: str | None, text: str | None,
) -> JdDocument:
    """Upsert a JD by its content hash (the identity — a re-uploaded identical JD is
    one document; an edit is a new one)."""
    jd = session.scalar(select(JdDocument).where(JdDocument.content_hash == content_hash))
    if jd is None:
        jd = JdDocument(content_hash=content_hash)
        session.add(jd)
    jd.title = title
    jd.source_ref = source_ref
    jd.media_type = media_type
    jd.text = text
    session.flush()
    return jd


def upsert_requisition(
    session: Session, *, req_code: str, role_title: str, job_location: str | None,
    jd_document_id: str | None, slug: str | None = None,
) -> RequisitionRow:
    """Upsert a role by its human handle (req_code)."""
    req = session.scalar(select(RequisitionRow).where(RequisitionRow.req_code == req_code))
    if req is None:
        req = RequisitionRow(req_code=req_code, status="active")
        session.add(req)
    req.role_title = role_title
    req.job_location = job_location
    req.jd_document_id = jd_document_id
    if slug:
        req.slug = slug
    session.flush()
    return req


def upsert_contact(session: Session, *, req_code: str, contact_role: str,
                   contact_email: str | None, contact_name: str | None = None) -> bool:
    """Attach a contact (hiring_manager | hr_spoc | recruiter) to a requisition by
    its req_code. Idempotent on (requisition, role, email). Returns True if a row was
    created. No-op when the requisition doesn't exist or the email is blank."""
    req = get_requisition_row(session, req_code)
    if req is None or not (contact_email or "").strip():
        return False
    email = contact_email.strip()
    existing = session.scalar(select(RequisitionContact).where(
        RequisitionContact.requisition_id == req.id,
        RequisitionContact.contact_role == contact_role,
        RequisitionContact.contact_email == email,
    ))
    if existing is not None:
        if contact_name:
            existing.contact_name = contact_name
        return False
    session.add(RequisitionContact(
        requisition_id=req.id, contact_role=contact_role,
        contact_email=email, contact_name=contact_name,
    ))
    session.flush()
    return True


def get_requisition_row(session: Session, req_code: str) -> RequisitionRow | None:
    return session.scalar(select(RequisitionRow).where(RequisitionRow.req_code == req_code))


def sync_role_details(session: Session, req_code: str, *, role_title: str | None = None,
                      job_location: str | None = None, jd_title: str | None = None,
                      jd_text: str | None = None) -> bool:
    """Write-through for PUT /api/roles/{id}/details when requisitions_backend=db:
    update the requisition row's IDENTITY fields + its JD document snapshot (only
    the provided fields). Lifecycle/hm/icims deliberately stay sidecar-only — the
    close/reopen/assign_hm pattern. The JD row's content_hash goes stale on a text
    edit and self-heals on the next `screener db import` (which upserts by hash).
    No-op returning False when the row doesn't exist."""
    req = get_requisition_row(session, req_code)
    if req is None:
        return False
    if role_title is not None:
        req.role_title = role_title
    if job_location is not None:
        req.job_location = job_location
    if (jd_title is not None or jd_text is not None) and req.jd_document_id:
        jd = session.get(JdDocument, req.jd_document_id)
        if jd is not None:
            if jd_title is not None:
                jd.title = jd_title
            if jd_text is not None:
                jd.text = jd_text
    session.flush()
    return True


def sync_new_role(session: Session, *, req_code: str, role_title: str,
                  job_location: str | None, jd_path: str | Path,
                  slug: str | None = None, created_by: str | None = None) -> Requisition:
    """Write-through for POST /api/roles when requisitions_backend=db.

    The create route writes the JD file + its sidecar; with the DB serving
    fetch_requisitions, a role that never becomes a ROW is invisible the moment it
    is created — the Requisitions list skips it and every per-req route 404s,
    including the checklist draft the New-requisition wizard calls next (which is
    exactly how this was found on the hosted app). So creation must land in both
    places, not just on disk.

    Deliberately the SAME pair of upserts `screener db import` performs, over the
    same JD bytes: the content hash matches, so a later import updates these rows
    rather than duplicating them.
    """
    path = Path(jd_path)
    raw = path.read_bytes()
    doc = upsert_jd_document(
        session,
        content_hash=hashlib.sha256(raw).hexdigest(),
        title=role_title,
        source_ref=str(path),
        media_type=path.suffix.lower().lstrip("."),
        text=raw.decode("utf-8", errors="replace"),
    )
    row = upsert_requisition(
        session, req_code=req_code, role_title=role_title,
        job_location=job_location, jd_document_id=doc.id, slug=slug,
    )
    if created_by:
        row.created_by = created_by
    session.flush()
    return _requisition_contract(row, doc)


def delete_requisition(session: Session, req_code: str) -> bool:
    """Write-through for DELETE /api/roles/{id} when requisitions_backend=db —
    the row counterpart of service.delete_requisition's file sweep. Without it a
    deleted role would keep serving from the DB with its JD gone.

    Rubric versions are removed BY req_code, not by FK: rows saved before the
    requisition existed carry a NULL requisition_id and would otherwise survive
    and be inherited by the next role reusing that (reusable) req_id. Runs and
    screening jobs are left alone — their FK is ON DELETE SET NULL by design, so
    the audit trail outlives the requisition. Returns False when there is no row.
    """
    req = get_requisition_row(session, req_code)
    if req is None:
        return False
    for rubric in session.scalars(select(RubricRow).where(RubricRow.req_code == req_code)):
        session.delete(rubric)  # ORM cascade takes the requirement children
    jd_document_id = req.jd_document_id
    session.delete(req)
    session.flush()
    # An identical JD is ONE document (hash identity), so it may still be in use.
    if jd_document_id and not session.scalar(
        select(func.count()).select_from(RequisitionRow)
        .where(RequisitionRow.jd_document_id == jd_document_id)
    ):
        doc = session.get(JdDocument, jd_document_id)
        if doc is not None:
            session.delete(doc)
            session.flush()
    return True


def backfill_requisition_links(session: Session) -> int:
    """Set requisition_id on rubric/run/screening_job rows that carry a matching
    req_code but no FK yet. Idempotent — only fills NULLs. Returns how many were
    linked. This is what turns the soft req_code links (runs/jobs Phase 4/5, rubrics
    Phase 6c) into real FKs once the requisitions exist."""
    reqs = {r.req_code: r.id for r in session.scalars(select(RequisitionRow))}
    linked = 0
    for model in (RubricRow, RunRow, ScreeningJobRow):
        rows = session.scalars(
            select(model).where(model.requisition_id.is_(None), model.req_code.is_not(None))
        )
        for row in rows:
            rid = reqs.get(row.req_code)
            if rid is not None:
                row.requisition_id = rid
                linked += 1
    session.flush()
    return linked
