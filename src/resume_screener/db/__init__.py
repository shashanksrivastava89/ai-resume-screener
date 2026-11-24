"""Database layer (Phase 0: foundation).

The engine and API stay the single source of truth for scoring; this package is
the persistence seam beneath them. Import surface:

- `Base`            — the SQLAlchemy declarative base (models subclass it)
- `get_engine`      — lazily-built engine from DATABASE_URL (Entra token for prod)
- `session_scope`   — context-manager session (CLI / scripts)
- `get_session`     — FastAPI dependency generator

Design note (the data model WILL evolve): integrity lives in the repository
layer and Alembic migrations, not in hard-to-change DB enums/triggers, so the
schema can change freely as requirements grow.
"""

from resume_screener.db.base import Base
from resume_screener.db.engine import get_engine
from resume_screener.db.session import get_session, session_scope

__all__ = ["Base", "get_engine", "get_session", "session_scope"]
