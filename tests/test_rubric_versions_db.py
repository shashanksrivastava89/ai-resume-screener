"""Checklist version history under RUBRIC_BACKEND=db.

The history reads version FILES (`*.rubric.vN.yaml`). The hosted app runs every
domain on Postgres, where there are no such files — so on 2026-09-07 the version
dialog came back empty there while working locally. These pin the DB path: the same
list, the same shape, the same "was anyone screened against it?" answer, and one
version reading back for a restore.

Offline: in-memory SQLite via tests/dbharness (a real-Postgres pass is the same
suite with TEST_DATABASE_URL set).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine as _test_engine
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A workspace whose RUBRIC backend is the database, everything else on files."""
    from fastapi.testclient import TestClient

    from resume_screener import rubric as rubric_mod
    from resume_screener.api.app import app
    from resume_screener.db import engine as engine_mod
    from resume_screener.db import session as session_mod

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)

    eng = _test_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(rubric_mod, "_use_db", lambda: True)
    try:
        yield TestClient(app)
    finally:
        eng.dispose()


V1 = [{"text": "US GAAP depth", "kind": "must_have", "importance": 4},
      {"text": "CA qualification", "kind": "hard_gate"}]
V2 = [{"text": "US GAAP depth", "kind": "must_have", "importance": 5},
      {"text": "CA qualification", "kind": "hard_gate"},
      {"text": "Oracle EBS exposure", "kind": "nice_to_have", "importance": 2}]


def _role(client) -> str:
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9200"})
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def _publish(client, req_id: str, rows: list[dict]) -> None:
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": rows})
    assert r.status_code == 200, r.text


def test_history_lists_db_versions_not_files(client):
    req_id = _role(client)
    _publish(client, req_id, V1)
    _publish(client, req_id, V2)
    # there are genuinely no version files — the rows ARE the history
    assert not list(Path("jds").glob("*.rubric.v*.yaml"))

    versions = client.get(f"/api/roles/{req_id}/rubric/versions",
                          headers=_as("TASpecialist")).json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]
    latest, first = versions
    assert latest["is_current"] is True and first["is_current"] is False
    assert (latest["requirement_count"], latest["gate_count"], latest["must_count"]) == (3, 1, 1)
    assert (first["requirement_count"], first["gate_count"], first["must_count"]) == (2, 1, 1)
    assert latest["published_by"] == "taspecialist@example.com"
    assert latest["published_ts"]          # the publish event, or the row's own stamp
    assert latest["screening_runs"] == 0   # nothing screened yet


def test_a_screened_version_says_so_on_the_db_path(client):
    from resume_screener.events import emit_event

    req_id = _role(client)
    _publish(client, req_id, V1)
    emit_event("screen_completed", actor="taspecialist@example.com", req_id=req_id,
               summary="Screened 3 candidates",
               data={"run_id": "run-1", "rubric": f"{req_id}.rubric.v1.yaml (v1)"})
    _publish(client, req_id, V2)

    versions = {v["version"]: v for v in
                client.get(f"/api/roles/{req_id}/rubric/versions",
                           headers=_as("TASpecialist")).json()["versions"]}
    assert versions[1]["screening_runs"] == 1 and versions[1]["last_screened_ts"]
    assert versions[2]["screening_runs"] == 0


def test_one_db_version_reads_back_for_a_restore(client):
    req_id = _role(client)
    _publish(client, req_id, V1)
    _publish(client, req_id, V2)

    got = client.get(f"/api/roles/{req_id}/rubric/versions/1",
                     headers=_as("TASpecialist")).json()
    assert [r["text"] for r in got["requirements"]] == ["US GAAP depth", "CA qualification"]
    assert got["requirements"][1]["kind"] == "hard_gate"
    assert client.get(f"/api/roles/{req_id}/rubric/versions/9",
                      headers=_as("TASpecialist")).status_code == 404


def test_a_flagged_row_stays_flagged_in_the_db_and_holds_screening(client):
    """The checklist critic's flag used to vanish on the DB path (no column for it),
    so a flagged checklist reloaded clean and screening was never held for the HR
    Head's sign-off (2026-10-16). The flag and its sentence now round-trip."""
    from resume_screener.api import service

    req_id = _role(client)
    rows = V1 + [{"text": "prefers candidates in Mumbai", "kind": "nice_to_have", "importance": 2}]
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": rows})
    assert r.status_code == 200 and r.json()["needs_approval"] is True

    got = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist")).json()
    flagged = [row for row in got["requirements"] if row["critic_level"] == "warn"]
    assert [row["text"] for row in flagged] == ["prefers candidates in Mumbai"]
    assert flagged[0]["critic_reason"]

    req = service.find_requisition(req_id)
    gate = service.checklist_gate(req, service.rubric_for(req))
    assert gate["ready"] is False and "HR Head approval" in gate["reason"]
