"""A read is not a write: `session_scope` must not clear the request memo.

Every repository read opens a `session_scope`, and it used to commit + clear the
request-scoped memo unconditionally. On a DB-backed deployment that meant each read
threw away the answers to all the previous ones, so `reqcache` — the centrepiece of
the 2026-09-05 performance pass — did nothing at all. It was invisible because the
benchmark and the whole suite ran the FILE backends, where the line never fires.

Measured on the pilot-shaped benchmark with all twelve domains on Postgres, ONE
dashboard load: 311 queries / 1,736 ms before, 166 / 399 ms after.

The read-after-write guarantee is the reason the clear exists, so it is pinned here
just as hard as the caching is.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import reqcache
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.models import AppEvent


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    try:
        yield maker
    finally:
        eng.dispose()


@pytest.fixture()
def memo():
    token = reqcache.begin()
    try:
        yield
    finally:
        reqcache.end(token)


def _counter():
    calls = {"n": 0}

    @reqcache.memo(key=lambda: "k")
    def read() -> int:
        calls["n"] += 1
        return calls["n"]

    return read, calls


def test_a_read_keeps_the_request_memo(db, memo):
    read, calls = _counter()
    assert read() == 1
    with session_mod.session_scope() as session:      # a pure read
        session.execute(text("select 1")).all()
    assert read() == 1, "a repository READ threw away the request's memoised answers"
    assert calls["n"] == 1


def test_a_write_still_drops_the_memo(db, memo):
    """The guarantee the clear exists for: a read AFTER a write in the same request
    must see the new data, never the memo from before it."""
    read, _ = _counter()
    assert read() == 1
    with session_mod.session_scope() as session:
        session.add(AppEvent(ts="2026-09-10T00:00:00", kind="test", req_id=None,
                             actor="t@example.com", data={}))
    assert read() == 2, "a WRITE must invalidate the request memo"


def test_a_deletion_counts_as_a_write(db, memo):
    with session_mod.session_scope() as session:
        session.add(AppEvent(ts="2026-09-10T00:00:00", kind="test", req_id=None,
                             actor="t@example.com", data={}))
    read, _ = _counter()
    assert read() == 1
    with session_mod.session_scope() as session:
        row = session.query(AppEvent).first()
        session.delete(row)
    assert read() == 2


def test_a_rolled_back_write_does_not_serve_stale_reads(db, memo):
    """Belt and braces: an exception rolls back, and the memo is left alone because
    nothing was committed."""
    read, _ = _counter()
    assert read() == 1
    with pytest.raises(RuntimeError):
        with session_mod.session_scope() as session:
            session.add(AppEvent(ts="2026-09-10T00:00:00", kind="test", req_id=None,
                                 actor="t@example.com", data={}))
            raise RuntimeError("boom")
    assert read() == 1
