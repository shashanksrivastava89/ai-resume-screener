"""Candidate + RequisitionCandidate — uniquely identifiable resumes and the JD<>resume batch.

`candidate.candidate_id` is the content-hash id (`candidate_id_for`), so the same
résumé is one identity everywhere and a re-export is a new identity.
RequisitionCandidate is the many-to-many attaching résumés to a requisition (the
per-role batch) — the same résumé can sit in several requisitions.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class Candidate(TimestampMixin, Base):
    __tablename__ = "candidate"

    # Natural PK: the résumé's content-hash id (16 hex from candidate_id_for).
    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Metadata only; the file bytes stay on disk (Blob at P26).
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)  # pre-redaction file stem


class RequisitionCandidate(PkMixin, TimestampMixin, Base):
    __tablename__ = "requisition_candidate"
    __table_args__ = (
        UniqueConstraint("requisition_id", "candidate_id", name="uq_req_candidate"),
    )

    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("requisition.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidate.candidate_id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(32), default="upload")  # upload|import|ats
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|removed
    file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
