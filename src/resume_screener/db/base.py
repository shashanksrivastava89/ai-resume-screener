"""The SQLAlchemy declarative base every ORM model subclasses.

Kept in its own tiny module so both the models and Alembic's env.py can import
`Base.metadata` without pulling in the engine (avoids import cycles).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models (added from Phase 1 onward)."""
