"""HmTake, ChecklistApproval, LegalHold — the remaining v2 file stores' DB homes (P49).

- HmTake mirrors output/feedback/hm_takes.jsonl: a hiring manager's read on a
  candidate (strong_yes/yes/no + note), INPUT to the recruiter, never a Decision.
  Latest-wins per (run_id, candidate_id, hm_email) — upserted, one row per key.
  It carries FKs to `requisition` and `app_user` per the ideal schema, but ALSO the
  soft `req_code`/`hm_email` strings (like RequisitionContact): a take can be
  recorded before the requisition row or the HM's user row exists (the file backend
  is the default and never populates those tables).

- ChecklistApproval mirrors jds/<stem>.approval.json (one row per requisition,
  keyed by req_code). LegalHold mirrors signals/legal_holds.json (one row per
  held requisition). Both are IDEAL-SCHEMA scaffolding: the tables exist so a later
  flip is a one-line switch, but P49 ships no backend switch/importer for them yet
  (their file stores remain the only code path) — see Part E.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class HmTake(PkMixin, TimestampMixin, Base):
    __tablename__ = "hm_take"
    __table_args__ = (
        UniqueConstraint("run_id", "candidate_id", "hm_email", name="uq_hm_take_key"),
    )

    # The app-generated public id the file store keys on.
    take_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # Soft string handle (the JD stem the API routes on) + the hard FK, set once the
    # requisition row exists (requisitions_backend=db).
    req_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    take: Mapped[str] = mapped_column(String(16))  # strong_yes | yes | no
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    hm_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    hm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ChecklistApproval(PkMixin, TimestampMixin, Base):
    __tablename__ = "checklist_approval"
    __table_args__ = (UniqueConstraint("req_code", name="uq_checklist_approval_req"),)

    req_code: Mapped[str] = mapped_column(String(64), index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="none")  # none | pending | approved
    flagged_requirement_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class LegalHold(PkMixin, TimestampMixin, Base):
    __tablename__ = "legal_hold"
    __table_args__ = (UniqueConstraint("req_id", name="uq_legal_hold_req"),)

    req_id: Mapped[str] = mapped_column(String(64), index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="SET NULL"), nullable=True, index=True
    )
    held: Mapped[bool] = mapped_column(Boolean, default=True)
    by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    released_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
