"""Runtime records: Run, Decision, ScreeningJob, AuditEntry, ReviewSignoff.

- Run indexes each `screener run` (region + jurisdiction + versions stamped).
- Decision mirrors the §4 Decision contract; append-only (the repo never updates
  or deletes — a correction is a new row; the required-reason rule is enforced in
  the repo, not a DB CHECK).
- ScreeningJob replaces the in-memory job dict (survives restarts).
- AuditEntry / ReviewSignoff are region-scoped so each region has its own trail
  and its own "pending HR + Legal review" state.

`region_id` on these tables means the GOVERNING region (see requisition.py): for a
Run/Decision it is the governing region of the job's jurisdiction (so the region's
HR Head/Legal can audit every job whose law they own, even ones a cross-region team
created); for AuditEntry/ReviewSignoff it is the region whose rules/config were
touched. Stored (denormalized) for fast regional filtering; the source of truth is
still the jurisdiction/config it derives from.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin

# Append-only logs (Decision, AuditEntry) key on a monotonic autoincrement id, so
# insertion order — and therefore "latest wins" — is unambiguous even when many
# rows land in the same clock tick (uuid PKs can't order by insertion). BIGINT in
# Postgres; plain INTEGER (rowid autoincrement) in SQLite for the offline tests.
_LOG_PK = BigInteger().with_variant(Integer, "sqlite")


class Run(PkMixin, TimestampMixin, Base):
    __tablename__ = "run"

    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # pipeline run id
    # The role's human handle / output-folder name (the slug the API routes on, e.g.
    # "accountant-sr-accountant-rtr-treasury"). A SOFT string link (like run_id and
    # Decision.run_id) so a run is addressable before the requisition table is
    # populated (Phase 6); the requisition_id FK below is set once it exists.
    req_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="SET NULL"), nullable=True, index=True
    )
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_pack: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rubric_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registry_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    versions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    bands: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    obligations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_utc: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ISO string, as stamped
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Decision(TimestampMixin, Base):
    __tablename__ = "decision"

    id: Mapped[int] = mapped_column(_LOG_PK, primary_key=True, autoincrement=True)
    # Soft string link to Run.run_id (kept as a string like the JSONL contract).
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="SET NULL"), nullable=True, index=True
    )
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewer_id: Mapped[str | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)  # email/name as recorded
    # The AI's band + score as shown to the reviewer ("Strong (91)"). NULL when the
    # row is sealed — see verdict_enc.
    suggestion: Mapped[str | None] = mapped_column(String(128), nullable=True)
    human_action: Mapped[str] = mapped_column(String(16))  # agree | override | rerank
    final_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The reviewer's note — required, enforced in the repo, not a DB CHECK. Stored as
    # "" when the row is sealed (the note then lives in verdict_enc).
    reason: Mapped[str] = mapped_column(Text)
    versions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ISO string, as recorded
    # The requisition (role slug) the decision was made on — the soft link the
    # carry-forward needs. Until 2026-10-14 the table had no such column, so on the DB
    # backend a re-screen never carried a decision forward.
    req_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # suggestion + reason encrypted together (fieldcrypto, InfoSec 2026-10-14):
    # "v1:<key version>:<base64>". NULL = a plain row (no key configured, or written
    # before one was) — reads handle both.
    verdict_enc: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScreeningJob(PkMixin, TimestampMixin, Base):
    __tablename__ = "screening_job"

    # The role's human handle / output-folder slug the API routes on. A SOFT string
    # link (like Run.req_code) so the Screen page can find "the active job for this
    # role" before the requisition table is populated (Phase 6); requisition_id is
    # set once it exists. The job's own id (PkMixin) is the public job_id the UI polls.
    req_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    requisition_id: Mapped[str | None] = mapped_column(
        ForeignKey("requisition.id", ondelete="SET NULL"), nullable=True, index=True
    )
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|error
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEntry(TimestampMixin, Base):
    __tablename__ = "audit_entry"

    id: Mapped[int] = mapped_column(_LOG_PK, primary_key=True, autoincrement=True)
    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # P51 second-leader approvals: who approved the change proposal that produced
    # this publish (NULL = a direct/unapproved publish — reopens the review banner),
    # and the change-request id linking back to signals/change_requests.jsonl.
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ReviewSignoff(PkMixin, TimestampMixin, Base):
    __tablename__ = "review_signoff"

    region_id: Mapped[str | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewers: Mapped[str] = mapped_column(String(512))
    review_date: Mapped[str] = mapped_column(String(40))
    recorded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
