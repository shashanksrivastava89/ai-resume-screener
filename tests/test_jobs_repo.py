"""Phase 5 tests — screening-job repository + the JOBS_BACKEND store switch.

Fully offline (in-memory SQLite, no Postgres, no Azure, no subprocess). Three layers:
1. the jobs repo directly (create/get/active, snapshot saves, reap, region stamp);
2. the in-memory store (unit) + the `_store()` switch that routes memory↔db;
3. the module read path (get_job / active_job_for / reap_orphans) with jobs_backend=db,
   proving the switch keeps the public job shape and gives a restart a clean recovery.

The runner (`_run`) shells out to `screener run`, so it's exercised via the stores,
not by spawning a real subprocess.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener.api import jobs as jobs_mod
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import jobs as repo
from resume_screener.db.repositories import regions as regions_repo




@pytest.fixture
def session() -> Session:
    eng = _sqlite_engine()
    sess = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield sess
    finally:
        sess.close()
        eng.dispose()


# --- repo directly -----------------------------------------------------------------


def test_create_and_get_job(session):
    repo.create_job(session, "job123456789", "accountant-role")
    session.commit()
    row = repo.get_job(session, "job123456789")
    assert row is not None
    assert row.id == "job123456789" and row.req_code == "accountant-role"
    assert row.status == "running" and row.done == 0 and row.total == 0
    assert row.message == "Starting…"


def test_job_public_shape(session):
    repo.create_job(session, "job123456789", "accountant-role")
    session.commit()
    assert repo.job_public(repo.get_job(session, "job123456789")) == {
        "job_id": "job123456789", "req_id": "accountant-role", "status": "running",
        "done": 0, "total": 0, "message": "Starting…", "error": "",
    }


def test_region_id_stamped(session):
    regions_repo.upsert_region(session, "APAC", "Asia-Pacific")
    session.flush()
    apac = regions_repo.get_region_by_code(session, "APAC").id
    repo.create_job(session, "j1", "in-role", region_id=apac)
    session.commit()
    assert repo.get_job(session, "j1").region_id == apac


def test_save_state_progress_then_done(session):
    repo.create_job(session, "j1", "accountant-role")
    repo.save_state(session, "j1", {"done": 3, "total": 8, "message": "Screening 3 of 8…"})
    session.commit()
    row = repo.get_job(session, "j1")
    assert row.done == 3 and row.total == 8 and row.status == "running"
    repo.save_state(session, "j1", {"status": "done", "done": 8, "message": "Scored 8 candidates."})
    session.commit()
    row = repo.get_job(session, "j1")
    assert row.status == "done" and row.done == 8 and row.message == "Scored 8 candidates."


def test_save_state_missing_row_is_noop(session):
    assert repo.save_state(session, "ghost", {"status": "done"}) is None


def test_active_job_for_only_running(session):
    repo.create_job(session, "running1", "role-a")
    repo.create_job(session, "done1", "role-a")
    repo.save_state(session, "done1", {"status": "done"})
    session.commit()
    active = repo.active_job_for(session, "role-a")
    assert active is not None and active.id == "running1"
    # a role with only a finished job has no active job
    repo.save_state(session, "running1", {"status": "done"})
    session.commit()
    assert repo.active_job_for(session, "role-a") is None
    assert repo.active_job_for(session, "no-such-role") is None


def test_reap_running_marks_interrupted(session):
    repo.create_job(session, "r1", "role-a")
    repo.create_job(session, "r2", "role-b")
    repo.create_job(session, "finished", "role-c")
    repo.save_state(session, "finished", {"status": "done"})
    session.commit()

    reaped = repo.reap_running(session)
    session.commit()
    assert reaped == 2  # only the two 'running' jobs
    for jid in ("r1", "r2"):
        row = repo.get_job(session, jid)
        assert row.status == "error"
        assert row.error == repo.INTERRUPTED_MESSAGE and row.message == repo.INTERRUPTED_MESSAGE
    assert repo.get_job(session, "finished").status == "done"  # untouched
    assert repo.reap_running(session) == 0  # nothing left to reap


# --- in-memory store + the _store() switch -----------------------------------------


def test_memory_store_roundtrip():
    store = jobs_mod._MemoryStore()
    store.create("j1", "role-a")
    assert store.read("j1")["status"] == "running"
    assert store.read_active("role-a")["job_id"] == "j1"
    store.save("j1", "role-a", {"status": "running", "done": 2, "total": 5,
                                "message": "Screening 2 of 5…", "error": ""})
    assert store.read("j1")["done"] == 2
    store.save("j1", "role-a", {"status": "done", "done": 5, "total": 5,
                                "message": "Scored 5 candidates.", "error": ""})
    assert store.read_active("role-a") is None  # no longer running
    assert store.read("nope") is None


def test_store_switch(monkeypatch):
    monkeypatch.setattr(jobs_mod, "_use_db", lambda: True)
    assert jobs_mod._store() is jobs_mod._DB
    monkeypatch.setattr(jobs_mod, "_use_db", lambda: False)
    assert jobs_mod._store() is jobs_mod._MEMORY


# --- module read path (jobs_backend=db) --------------------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    """Point the DB helpers + the jobs module at an in-memory SQLite job store."""
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(jobs_mod, "_use_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


def test_module_get_and_active_from_db(db_backend):
    with session_mod.session_scope() as s:
        repo.create_job(s, "job123456789", "accountant-role")
    got = jobs_mod.get_job("job123456789")
    assert got is not None and got["job_id"] == "job123456789"
    assert got["req_id"] == "accountant-role" and got["status"] == "running"
    assert jobs_mod.active_job_for("accountant-role")["job_id"] == "job123456789"
    assert jobs_mod.get_job("no-such-job") is None


def test_module_reap_orphans_when_db(db_backend):
    with session_mod.session_scope() as s:
        repo.create_job(s, "orphan1", "role-a")
        repo.create_job(s, "orphan2", "role-b")
    assert jobs_mod.reap_orphans() == 2
    assert jobs_mod.get_job("orphan1")["status"] == "error"
    assert jobs_mod.active_job_for("role-a") is None  # no longer blocks a new screen


def test_reap_orphans_noop_for_memory(monkeypatch):
    monkeypatch.setattr(jobs_mod, "_use_db", lambda: False)
    assert jobs_mod.reap_orphans() == 0
