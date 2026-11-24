"""AppEvent — the DB mirror of the app-event spine (P49).

`signals/events.jsonl` (events.py) is the ONE requisition/app-event log — close/
reopen/snooze, screening completions, preference transitions, HM takes, flag
changes, history views. When EVENTS_BACKEND=db it lands here instead.

Like the other append-only logs (Decision, AuditEntry), row ORDER — not the
coarse wall-clock `ts` — is the source of "what came after", so this keys on a
monotonic autoincrement id (BIGINT in Postgres, INTEGER rowid in SQLite). The
explorer reads oldest-first by that id.
"""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from resume_screener.db.base import Base
from resume_screener.db.models.common import TimestampMixin

_LOG_PK = BigInteger().with_variant(Integer, "sqlite")


class AppEvent(TimestampMixin, Base):
    __tablename__ = "app_event"

    id: Mapped[int] = mapped_column(_LOG_PK, primary_key=True, autoincrement=True)
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ISO string, as stamped
    kind: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    req_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
