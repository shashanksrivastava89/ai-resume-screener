"""A requisition created through the API must EXIST under requisitions_backend=db.

The hosted (UAT) regression this pins: POST /api/roles wrote only the JD file +
sidecar, while fetch_requisitions was serving DB rows. The new requisition was
therefore invisible the instant it was created — it never appeared in the
Requisitions list, and every per-req route 404'd, including the checklist draft
the New-requisition wizard calls next. The wizard catches that failure and falls
back to one empty row, which is what a recruiter sees: a blank checklist.

Offline: tmp_path workspace + an in-memory SQLite engine, like the repo suite.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine
from tests.test_sso_identity import _as

from resume_screener.ats import local_folder
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import requisitions as repo

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def db_backend(monkeypatch):
    """requisitions_backend=db, wired to a throwaway engine."""
    eng = make_test_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(local_folder, "_use_requisitions_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


@pytest.fixture()
def client(workspace, db_backend):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _create(client, **extra):
    return client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Finance Analyst", "jd_text": "Owns treasury accounting.",
        "job_location": "IN", "icims_req_id": "IN-901", **extra,
    })


def test_created_requisition_is_immediately_visible_and_addressable(client, workspace):
    created = _create(client)
    assert created.status_code == 200, created.text
    req_id = created.json()["req_id"]

    listed = client.get("/api/roles", headers=_as("TASpecialist"))
    assert [r["req_id"] for r in listed.json()["roles"]] == [req_id]

    # The route the New-requisition wizard hits next: 404 here is the blank checklist.
    rubric = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"))
    assert rubric.status_code == 200, rubric.text
    assert rubric.json()["requirements"] == []  # nothing drafted yet, but the req EXISTS

    # The JD the draft call reads must resolve through the row's jd_ref.
    detail = client.get(f"/api/roles/{req_id}/jd", headers=_as("TASpecialist"))
    assert detail.status_code == 200
    assert "treasury accounting" in detail.json()["jd_text"]


def test_the_duplicate_icims_guard_sees_db_backed_requisitions(client):
    assert _create(client).status_code == 200
    clash = _create(client, title="Finance Analyst II")
    assert clash.status_code == 409  # the guard reads the same list — no silent twin


def test_delete_removes_the_row_too(client, workspace):
    req_id = _create(client).json()["req_id"]
    gone = client.request("DELETE", f"/api/roles/{req_id}",
                          headers=_as("TASpecialist"), json={"confirm": True})
    assert gone.status_code == 200, gone.text

    assert client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"] == []
    with session_mod.session_scope() as session:
        assert repo.get_requisition(session, req_id) is None
    # The iCIMS id is free again — the row is really gone, not just the file.
    assert _create(client).status_code == 200


def test_a_failed_db_write_creates_nothing_at_all(client, workspace, monkeypatch):
    """Half-created is the orphan state this write-through exists to prevent."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(repo, "sync_new_role", boom)
    failed = _create(client)
    assert failed.status_code == 503
    assert sorted(p.name for p in (workspace / "jds").iterdir()) == []
