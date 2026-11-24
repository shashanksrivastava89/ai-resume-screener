"""JdDocument, Requisition, RequisitionContact.

Uniquely identifiable JDs: `jd_document.content_hash` is UNIQUE and the
`requisition` carries its own surrogate PK plus a human `req_code` — two roles can
share a title and still be distinct.

REGION vs JURISDICTION are decoupled from where the staff sit. The law that
governs a job is fixed by the JOB's location, never by where the HR/TA/HM is:
- `job_location` (a jurisdiction code) is the single source of truth for the legal
  rules/policy pack. A US-CA recruiter hiring in India sets `job_location = IN` and
  the IN pack applies.
- the GOVERNING region (which Regional HR Head/Legal owns that job's rules + audit)
  is DERIVED from the jurisdiction (`Jurisdiction.region_id`), not stored here, so
  it can never drift.
- `origin_region_id` is the region the CREATOR was operating in (their chosen
  hiring region at login) — e.g. Americas for that US-CA recruiter. It records
  "who is running this req", separately from "whose law applies".
- contacts (RequisitionContact: hiring_manager / hr_spoc / recruiter) are people
  records independent of the job's region — a hiring manager may sit in any region;
  their own region comes from their user grants. This is also the hook for
  extending scoped access to Hiring Managers.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class JdDocument(PkMixin, TimestampMixin, Base):
    __tablename__ = "jd_document"

    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # path / ATS id
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)


class Requisition(PkMixin, TimestampMixin, Base):
    __tablename__ = "requisition"

    req_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # human handle
    slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role_title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="draft")

    jd_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("jd_document.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The legal mapping — the jurisdiction/policy pack that governs this role, and
    # the sole source of the GOVERNING region (via Jurisdiction.region_id). Soft
    # string link to jurisdiction.code (kept as a plain string so a role can carry a
    # code before that jurisdiction row exists; integrity checked in app).
    job_location: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # The creating team's operating region (their chosen hiring region) — NOT the
    # governing region. Cross-region hiring: origin_region_id != governing region.
    origin_region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    contacts: Mapped[list["RequisitionContact"]] = relationship(
        back_populates="requisition", cascade="all, delete-orphan"
    )


class RequisitionContact(PkMixin, TimestampMixin, Base):
    __tablename__ = "requisition_contact"
    __table_args__ = (
        UniqueConstraint(
            "requisition_id", "contact_role", "contact_email", name="uq_req_contact"
        ),
    )

    requisition_id: Mapped[str] = mapped_column(
        ForeignKey("requisition.id", ondelete="CASCADE"), index=True
    )
    contact_role: Mapped[str] = mapped_column(String(32))  # hiring_manager | hr_spoc | recruiter
    # A contact may or may not be an app user yet (HM not onboarded) — hence both a
    # soft FK and captured name/email.
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    requisition: Mapped["Requisition"] = relationship(back_populates="contacts")
