"""An in-memory SQLite DATABASE_URL means "no database" (2026-07-30).

`sqlite://` (and `sqlite:///:memory:`) creates an EMPTY schema-less database per
connection: it can never be migrated, so a `*_BACKEND=db` switch pointed at it is
a contradiction. Left unguarded, every DB-backed read raised
"no such table: requisition" / "no such table: policy_pack" — an opaque 500 that
took the whole dashboard down (the Requisitions list, /api/locations and every
role card) while the non-DB endpoints kept working.

The database is a seam UNDER the engine whose documented default is zero-DB-infra
behavior, so the honest resolution is to serve the file/JSONL stores and log it
once — never to 500. A REAL database (Postgres, or a SQLite file) is untouched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import config as config_mod
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

DB_SWITCHES = {
    "REQUISITIONS_BACKEND": "db", "POLICY_BACKEND": "db",
    "GOVERNANCE_BACKEND": "db", "EVENTS_BACKEND": "db",
    "FEEDBACK_BACKEND": "db", "RUBRIC_BACKEND": "db",
}


@pytest.fixture(autouse=True)
def _fresh_warn_state(monkeypatch):
    """The notice is logged once per (url, switches) — isolate that memo."""
    monkeypatch.setattr(config_mod, "_WARNED_MEMORY_DB", set())


def _set_db_env(monkeypatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    for key, value in DB_SWITCHES.items():
        monkeypatch.setenv(key, value)


# --- the guard itself ---------------------------------------------------------------


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_memory_sqlite_coerces_every_backend_to_its_file_default(monkeypatch, url):
    _set_db_env(monkeypatch, url)
    s = config_mod.load_db_settings()
    assert s.requisitions_backend == "file"
    assert s.policy_backend == "file"
    assert s.rubric_backend == "file"
    assert s.governance_backend == "jsonl"
    assert s.events_backend == "jsonl"
    assert s.feedback_backend == "jsonl"


def test_a_real_database_url_keeps_the_db_backends(monkeypatch):
    """Postgres (or any real target) is untouched — the flip still works."""
    _set_db_env(monkeypatch, "postgresql+psycopg://screener:screener@localhost:5432/screener")
    s = config_mod.load_db_settings()
    assert s.requisitions_backend == "db"
    assert s.policy_backend == "db"
    assert s.governance_backend == "db"


def test_a_sqlite_FILE_url_keeps_the_db_backends(monkeypatch, tmp_path):
    """A sqlite FILE can be migrated and persists across connections — only the
    in-memory form is the 'no database' signal."""
    _set_db_env(monkeypatch, f"sqlite:///{(tmp_path / 'screener.db').as_posix()}")
    s = config_mod.load_db_settings()
    assert s.requisitions_backend == "db"
    assert s.policy_backend == "db"


def test_memory_sqlite_without_db_switches_is_unchanged(monkeypatch):
    """No switches set = the ordinary zero-DB default; nothing to warn about."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    for key in DB_SWITCHES:
        monkeypatch.delenv(key, raising=False)
    s = config_mod.load_db_settings()
    assert (s.requisitions_backend, s.policy_backend) == ("file", "file")


def test_the_notice_is_logged_once_per_config(monkeypatch, caplog):
    """load_db_settings runs many times per request — the warning must not spam."""
    _set_db_env(monkeypatch, "sqlite://")
    with caplog.at_level("WARNING", logger="resume_screener.config"):
        for _ in range(25):
            config_mod.load_db_settings()
    notices = [r for r in caplog.records if "in-memory SQLite" in r.getMessage()]
    assert len(notices) == 1
    assert "REQUISITIONS_BACKEND" in notices[0].getMessage()


# --- the dashboard the bug took down -------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)

    from resume_screener.api.app import app

    return TestClient(app)


def test_roles_and_locations_serve_200_with_db_switches_on_memory_sqlite(client, monkeypatch):
    """The exact reported failure: /api/roles and /api/locations 500'd with
    'no such table'. They must serve the file stores instead."""
    _set_db_env(monkeypatch, "sqlite://")
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "IN-4242",
    })
    assert created.status_code == 200, created.text

    roles = client.get("/api/roles", headers=_as("TASpecialist"))
    assert roles.status_code == 200, roles.text
    assert [r["req_id"] for r in roles.json()["roles"]] == ["treasury-analyst"]

    locations = client.get("/api/locations", headers=_as("TASpecialist"))
    assert locations.status_code == 200, locations.text
    assert any(loc["code"] == "IN" for loc in locations.json()["packs"])


# --- the engine's SQLite threading fix ------------------------------------------------


def test_memory_sqlite_engine_is_thread_shareable():
    """A threaded server runs sync routes on ROTATING threadpool threads. Without
    StaticPool + check_same_thread=False, in-memory SQLite raised 'SQLite objects
    created in a thread can only be used in that same thread' (noisy pool errors
    that escalate to request-path 500s on a long-lived worker)."""
    from sqlalchemy.pool import StaticPool

    from resume_screener.db.engine import get_engine, reset_engine

    reset_engine()
    try:
        engine = get_engine("sqlite://")
        assert isinstance(engine.pool, StaticPool)
        assert engine.dialect.is_async is False
        # the connection really is usable from another thread
        import threading

        from sqlalchemy import text

        errors: list[str] = []
        # Serialized on purpose. StaticPool hands every thread the SAME underlying
        # sqlite3 connection, and one sqlite3 connection is not safe for CONCURRENT
        # use — 8 threads querying it simultaneously sporadically raise internal
        # errors like "tuple index out of range". That made this test flaky in CI
        # (it failed on a contended runner while passing 6/6 locally on identical
        # code). The contract under test is cross-thread REUSE — that a connection
        # created on one thread works on another — not concurrency sqlite never
        # promised. The app itself is unaffected: sqlite is a dev/test-only backend;
        # the deployed app runs on PostgreSQL with a normal pooled connection.
        lock = threading.Lock()

        def touch() -> None:
            try:
                with lock, engine.connect() as conn:
                    conn.execute(text("select 1")).scalar()
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        threads = [threading.Thread(target=touch) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Still meaningful: all 8 ran on threads other than the creating one, so a
        # check_same_thread regression would surface here as it did originally.
        assert errors == []
    finally:
        reset_engine()
