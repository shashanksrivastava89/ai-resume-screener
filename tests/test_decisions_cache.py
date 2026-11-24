"""The decision log is read once per version, not once per request.

The file backend has always cached the parsed log on the file's (size, mtime). The DB
backend had nothing: every request that touched a decision ran a full-table SELECT and
revalidated every row — a cost that grows with the deployment's whole decision
history rather than with anything on screen (2026-09-10).

The log is append-only with an autoincrementing key, so (count, max id) is an exact
version, not a heuristic. These pin both halves: it is reused when nothing changed,
and it is never reused when something did.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import feedback
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import decisions as decisions_repo
from resume_screener.models import Decision


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(feedback, "_use_db", lambda: True)
    feedback.invalidate_decisions()
    try:
        yield maker
    finally:
        eng.dispose()


def _decide(candidate_id: str) -> Decision:
    return Decision(candidate_id=candidate_id, run_id="r1", req_id="alpha",
                    suggestion="Strong", human_action="agree",
                    reason="looks right", reviewer="ta@example.com",
                    versions={}, ts="2026-09-10T00:00:00")


def _reads(monkeypatch) -> list[int]:
    seen: list[int] = []
    real = decisions_repo.list_decisions
    monkeypatch.setattr(decisions_repo, "list_decisions",
                        lambda session: (seen.append(1), real(session))[1])
    return seen


def test_an_unchanged_log_is_parsed_once(db, monkeypatch):
    feedback.record_decision(_decide("c1"))
    feedback.invalidate_decisions()
    seen = _reads(monkeypatch)

    assert len(feedback.load_decisions.__wrapped__()) == 1
    assert len(feedback.load_decisions.__wrapped__()) == 1
    assert len(seen) == 1, f"the log was re-read {len(seen)} times without changing"


def test_an_append_is_never_missed(db, monkeypatch):
    feedback.record_decision(_decide("c1"))
    assert len(feedback.load_decisions.__wrapped__()) == 1
    feedback.record_decision(_decide("c2"))
    assert len(feedback.load_decisions.__wrapped__()) == 2, "an appended decision was missed"


def test_the_stamp_moves_on_every_append(db):
    with session_mod.session_scope() as session:
        before = decisions_repo.table_stamp(session)
    feedback.record_decision(_decide("c1"))
    with session_mod.session_scope() as session:
        after = decisions_repo.table_stamp(session)
    assert after != before and after[0] == before[0] + 1
