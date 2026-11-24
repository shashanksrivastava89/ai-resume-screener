"""DELETE /api/roles/{id} (permanent requisition delete with loss counts) and
the server-side screen gate that now also requires a non-empty job description.

Offline: tmp_path workspace + TestClient/_as(), mirroring test_role_details_api.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener.events import load_events
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

JD_BODY = "Owns treasury accounting and audits.\n\nResponsibilities:\n- GST/TDS filings"


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


def _mk_role(client, title="Finance Analyst", icims="IN-801"):
    response = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": title, "jd_text": JD_BODY, "job_location": "IN", "icims_req_id": icims,
    })
    assert response.status_code == 200, response.text
    return response.json()["req_id"]


def _add_resume(client, req_id, name="anita-rao.pdf"):
    r = client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"),
                    files=[("files", (name, b"%PDF-1.4 synthetic", "application/pdf"))])
    assert r.status_code == 200


# --- DELETE ---------------------------------------------------------------------------


def test_delete_removes_the_role_and_all_its_files(client, workspace):
    req_id = _mk_role(client)
    _add_resume(client, req_id)
    assert (workspace / "jds" / f"{req_id}.md").exists()
    assert (workspace / "jds" / f"{req_id}.req.json").exists()
    assert (workspace / "data" / "roles" / req_id).exists()

    resp = client.request("DELETE", f"/api/roles/{req_id}",
                          headers=_as("TASpecialist"), json={"confirm": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == req_id

    # JD + sidecar + batch are gone; the role no longer resolves.
    assert not (workspace / "jds" / f"{req_id}.md").exists()
    assert not (workspace / "jds" / f"{req_id}.req.json").exists()
    assert not (workspace / "data" / "roles" / req_id).exists()
    assert client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).status_code == 404


def test_delete_requires_confirmation(client):
    req_id = _mk_role(client)
    resp = client.request("DELETE", f"/api/roles/{req_id}",
                          headers=_as("TASpecialist"), json={})
    assert resp.status_code == 400
    # untouched
    assert client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).status_code == 200


def test_delete_records_an_event_that_survives_the_delete(client):
    req_id = _mk_role(client, title="To Be Removed", icims="IN-909")
    client.request("DELETE", f"/api/roles/{req_id}",
                   headers=_as("TASpecialist"), json={"confirm": True})
    kinds = [e.get("kind") or e.get("action") for e in load_events()]
    assert "req_deleted" in kinds


def test_delete_of_one_req_leaves_a_prefix_sibling_intact(client, workspace):
    # 'accountant' vs 'accountant-treasury' — the trailing-dot glob must not wipe
    # the sibling whose id is a prefix of the deleted one.
    a = _mk_role(client, title="Accountant", icims="IN-1")
    b = _mk_role(client, title="Accountant Treasury", icims="IN-2")
    client.request("DELETE", f"/api/roles/{a}", headers=_as("TASpecialist"),
                   json={"confirm": True})
    assert client.get(f"/api/roles/{b}", headers=_as("TASpecialist")).status_code == 200
    assert (workspace / "jds" / f"{b}.md").exists()


# --- screen gate: JD required ---------------------------------------------------------


def test_screen_blocked_when_jd_is_blank(client, workspace):
    req_id = _mk_role(client)
    _add_resume(client, req_id)
    # Blank out the JD body on disk (keep the heading only).
    (workspace / "jds" / f"{req_id}.md").write_text("# Finance Analyst\n", encoding="utf-8")

    # jd_present reflects the empty body...
    detail = client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).json()
    assert detail["jd_present"] is False

    # ...and screening is refused server-side (before any run).
    resp = client.post(f"/api/roles/{req_id}/screen", headers=_as("TASpecialist"), json={})
    assert resp.status_code == 400
    assert "job description" in resp.json()["detail"].lower()


def test_jd_present_true_for_a_normal_role(client):
    req_id = _mk_role(client)
    detail = client.get(f"/api/roles/{req_id}", headers=_as("TASpecialist")).json()
    assert detail["jd_present"] is True


# --- screen/active: progress-bar re-attach --------------------------------------------


def test_screen_active_is_null_when_no_run_is_in_flight(client):
    req_id = _mk_role(client)
    resp = client.get(f"/api/roles/{req_id}/screen/active", headers=_as("TASpecialist"))
    assert resp.status_code == 200
    # nothing running AND nothing ever run for a fresh role — both null
    assert resp.json() == {"job": None, "last": None}


def test_screen_active_returns_the_running_job(client, monkeypatch):
    req_id = _mk_role(client)
    fake = {"job_id": "j1", "req_id": req_id, "status": "running",
            "done": 2, "total": 3, "message": "Screening 2 of 3…", "error": ""}
    from resume_screener.api import jobs
    monkeypatch.setattr(jobs, "active_job_for", lambda rid: fake if rid == req_id else None)
    resp = client.get(f"/api/roles/{req_id}/screen/active", headers=_as("TASpecialist"))
    assert resp.status_code == 200
    assert resp.json()["job"]["status"] == "running"
