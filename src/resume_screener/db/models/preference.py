"""HrPreference — a user-scoped HR screening preference (P49 ideal schema).

GREENFIELD RESHAPE: the speculative `hr_preference_set`/`hr_preference_override`
JD↔signal-override tables (never wired to any code path) are DROPPED and replaced
by the shape the P39/P40 feature actually uses — one row per preference, folded to
its latest state (the file store, signals/preferences.jsonl, is event-sourced;
the DB stores the current fold, upserted by `pref_id`, latest-wins).

A preference is a plain-language nudge the owner wants applied to every requisition
they manage — classified by the fairness check (ok and warn → pending, blocked →
blocked; 2026-10-16), applied only once an HR Head other than its owner approves it,
and bounded to +2 points by the P40 scoring stage. A row stored "active" before then
reads as pending (preferences._display_status). The repository converts rows ⇄ the same dict the file store folds,
so callers stay backend-agnostic.
"""

from __future__ import annotations

from sqlalchemy import JSON, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from resume_screener.db.base import Base
from resume_screener.db.models.common import PkMixin, TimestampMixin


class HrPreference(PkMixin, TimestampMixin, Base):
    __tablename__ = "hr_preference"

    # The app-generated public id (12 hex) the file store keys on; unique so an
    # upsert (latest-wins fold) targets exactly one row.
    pref_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    owner_email: Mapped[str] = mapped_column(String(255), index=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)  # add|approve|decline|withdraw
    status: Mapped[str] = mapped_column(String(16), index=True)  # active|pending|approved|blocked|declined|withdrawn
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # the TextCheck dump
    cap_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_due: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decline_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[str | None] = mapped_column(String(40), nullable=True)
