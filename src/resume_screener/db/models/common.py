"""Shared building blocks for ORM models.

Deliberately soft so the model can evolve (your standing instruction):
- surrogate PKs are app-generated uuid hex strings (portable across Postgres and
  the SQLite used in tests; no DB-specific default);
- enum-like columns are plain strings validated in the app/repository layer —
  NO database ENUM types (adding a value must never need a migration);
- evolving nested data uses JSON columns;
- there are NO CHECK constraints or triggers. Integrity that must hold (append-
  only logs, required reasons) is enforced in the repository layer, which is
  trivially changeable, not baked into the schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


def new_uuid() -> str:
    """Surrogate primary-key value: a random uuid4 as 32 hex chars."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PkMixin:
    """A string surrogate primary key, generated in Python."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)


class TimestampMixin:
    """created_at / updated_at, tz-aware."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
