"""B2: the P43 approval rule enforced SERVER-side — a critic-flagged checklist
can't screen until the HR Head signs that exact version off. checklist_ready /
checklist_block_reason ride the role payload so the UI and /screen can't drift
(service.checklist_gate is the single source both read).

Offline: tmp_path workspace + TestClient/_as(); jobs.start_screening stubbed
(no Azure). Warn/clean rows reuse tests/test_checklist_critic.py's fixtures.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_checklist_critic import CLEAN, WARN_ROW
from tests.test_sso_identity import _as

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
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


@pytest.fixture()
def screen_stub(monkeypatch):
    """Stub the job runner so a gate-passing /screen never touches Azure."""
    from resume_screener.api import jobs

    calls: list[str] = []

    def fake_start(req_id, jd_ref, **kwargs):
        calls.append(req_id)
        return {"job_id": "job-1", "status": "running"}

    monkeypatch.setattr(jobs, "start_screening", fake_start)
    return calls


def _mk_role(client, icims="2026-9200") -> str:
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": icims,
    })
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def _add_resume(client, req_id, name="anita-rao.pdf"):
    response = client.post(
        f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"),
        files=[("files", (name, b"%PDF-1.4 synthetic", "application/pdf"))])
    assert response.status_code == 200


APPROVAL_SENTENCE = "This checklist needs HR Head approval before screening."


def test_flagged_unapproved_checklist_blocks_screening(client, screen_stub):
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})
    _add_resume(client, req_id)

    role = client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).json()
    assert role["checklist_ready"] is False
    assert role["checklist_block_reason"] == APPROVAL_SENTENCE

    blocked = client.post(f"/api/roles/{req_id}/screen", json={},
                          headers=_as("TASpecialist"))
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == APPROVAL_SENTENCE
    assert screen_stub == []  # the job runner was never reached

    # the list card carries the same gate
    [card] = client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]
    assert card["checklist_ready"] is False
    assert card["checklist_block_reason"] == APPROVAL_SENTENCE


def test_approval_unblocks_screening(client, screen_stub):
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})
    _add_resume(client, req_id)
    client.post(f"/api/roles/{req_id}/checklist-approval/request",
                headers=_as("TASpecialist"))
    client.post(f"/api/roles/{req_id}/checklist-approval/approve",
                headers=_as("HRHead"))

    role = client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).json()
    assert role["checklist_ready"] is True
    assert role["checklist_block_reason"] is None

    ok = client.post(f"/api/roles/{req_id}/screen", json={}, headers=_as("TASpecialist"))
    assert ok.status_code == 200, ok.text
    assert screen_stub == [req_id]


def test_clean_checklist_is_ready_and_screens(client, screen_stub):
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": CLEAN})
    _add_resume(client, req_id)

    role = client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).json()
    assert role["checklist_ready"] is True
    assert role["checklist_block_reason"] is None

    ok = client.post(f"/api/roles/{req_id}/screen", json={}, headers=_as("TASpecialist"))
    assert ok.status_code == 200, ok.text
    assert screen_stub == [req_id]


def test_no_checklist_is_not_ready_but_carries_no_reason(client):
    """The no-checklist case is carried by the stage/CTA copy — the block reason
    is reserved for the approval hold."""
    req_id = _mk_role(client)
    _add_resume(client, req_id)
    role = client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).json()
    assert role["checklist_ready"] is False
    assert role["checklist_block_reason"] is None
    blocked = client.post(f"/api/roles/{req_id}/screen", json={},
                          headers=_as("TASpecialist"))
    assert blocked.status_code == 400
    assert "checklist" in blocked.json()["detail"].lower()


def test_republish_reopens_the_gate(client, screen_stub):
    """A fresh flagged version needs a FRESH sign-off — approving v1 never
    unlocks v2 (checklist_approval.status_for is version-exact)."""
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})
    _add_resume(client, req_id)
    client.post(f"/api/roles/{req_id}/checklist-approval/request",
                headers=_as("TASpecialist"))
    client.post(f"/api/roles/{req_id}/checklist-approval/approve",
                headers=_as("HRHead"))
    # republish (still flagged) → v2 → gate closes again
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})
    blocked = client.post(f"/api/roles/{req_id}/screen", json={},
                          headers=_as("TASpecialist"))
    assert blocked.status_code == 400
    assert blocked.json()["detail"] == APPROVAL_SENTENCE
    assert screen_stub == []
