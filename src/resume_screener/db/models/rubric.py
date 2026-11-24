"""Rubric (screening criteria) + Requirement — the JD<>criteria mapping.

Versioned per requisition (UNIQUE(requisition_id, version)); a UI edit saves a new
version. Requirements are owned children (ORM cascade delete-orphan — an app-level
rule, not a rigid DB cascade). Mirrors the §4 Pydantic Rubric: ONE ordered
requirements list (P33) — a hard gate is a requirement row with kind='hard_gate'
and weight 0; the separate hard_filter table is gone.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class Rubric(PkMixin, TimestampMixin, Base):
    __tablename__ = "rubric"
    __table_args__ = (
        UniqueConstraint("requisition_id", "version", name="uq_rubric_version"),
        # The natural key while the requisition table is still empty (Phase 6c): a
        # rubric is versioned per role slug. Once Phase 6d populates requisitions,
        # requisition_id is backfilled and both keys agree (1:1 req_code↔requisition).
        UniqueConstraint("req_code", "version", name="uq_rubric_reqcode_version"),
    )

    # The role slug / JD stem the API routes on (a SOFT string link like run.req_code
    # and screening_job.req_code); the requisition_id FK is set once that row exists.
    req_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="CASCADE"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    role_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="approved")  # draft | approved
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="rubric", cascade="all, delete-orphan"
    )


class Requirement(PkMixin, Base):
    __tablename__ = "requirement"

    rubric_id: Mapped[str] = mapped_column(
        ForeignKey("rubric.id", ondelete="CASCADE"), index=True
    )
    handle: Mapped[str | None] = mapped_column(String(16), nullable=True)  # r01, r02, …
    text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(32), default="other")
    kind: Mapped[str] = mapped_column(String(16), default="nice_to_have")  # nice_to_have|must_have|hard_gate
    weight: Mapped[float] = mapped_column(Float, default=0.0)  # gates always 0
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    # The checklist critic's flag and its sentence (models.Requirement). Without
    # these the database dropped the flag and the screen gate never held
    # (2026-10-16, migration d7f3a9b2c5e8).
    critic_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    critic_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    rubric: Mapped["Rubric"] = relationship(back_populates="requirements")
