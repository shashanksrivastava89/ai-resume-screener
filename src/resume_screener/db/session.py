"""Session helpers: one for scripts/CLI, one for FastAPI.

Both wrap the same unit-of-work: commit on success, roll back on error, always
close. Repositories take a `Session` argument (never build their own), so call
sites own the transaction boundary.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from resume_screener.db.engine import get_engine

_makers: dict[int, sessionmaker] = {}


def _maker() -> sessionmaker:
    engine = get_engine()
    maker = _makers.get(id(engine))
    if maker is None:
        maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
        _makers[id(engine)] = maker
    return maker


def _wrote(session: Session) -> bool:
    """Did this unit of work actually change anything?

    `dirty` is "possibly modified", so this errs towards True — over-clearing costs
    a re-read, under-clearing would serve stale data after a write, and only one of
    those is a correctness bug."""
    return bool(session.new or session.dirty or session.deleted)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for CLI/scripts: `with session_scope() as s: ...`.

    A WRITE drops the request-scoped read memo (reqcache), the same way file_lock
    does for the file stores, so a DB-backed deployment has the same read-after-write
    guarantee as a file-backed one.

    A READ must not (2026-09-10). Every repository read opens one of these, and
    clearing the memo unconditionally meant each read threw away the answers to all
    the previous ones — so on a DB-backed deployment the request memo did nothing at
    all. Measured on the pilot-shaped benchmark with all twelve domains on Postgres:
    ONE dashboard load ran `list_decisions` 28 times, `list_requisitions` 26 times
    and the rubric queries 39 times, for 311 queries and 1,736 ms. The 2026-09-05
    perf pass was measured in file mode, where this line never fires — which is
    exactly how it survived."""
    session = _maker()()
    try:
        yield session
        wrote = _wrote(session)
        session.commit()
        if wrote:
            _clear_request_memo()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency (generator): `session = Depends(get_session)`."""
    session = _maker()()
    try:
        yield session
        wrote = _wrote(session)
        session.commit()
        if wrote:
            _clear_request_memo()   # see session_scope: a read is not a write
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _clear_request_memo() -> None:
    from resume_screener import reqcache

    reqcache.clear()
