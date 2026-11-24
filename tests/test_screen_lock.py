"""The one-run-per-requisition lock (2026-07-19).

While a screening run is LIVE or PAUSED: nobody may start another run or change
the résumé batch (add / import / remove / clear all 409 — for everyone, the
initiator included), and only the run's INITIATOR or a Super Admin may pause,
cancel or resume it. Cancelling releases the lock so the batch can change; the
next screen starts fresh (cache still reused).

Offline: real routes over the memory job store (no subprocess is ever spawned —
a running lock returns the existing job idempotently, a paused one is finalized
in place).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener.api import jobs
from resume_screener.ats.local_folder import save_req_meta
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]
STEM = "finance-analyst"
TA_EMAIL = "taspecialist@example.com"  # matches _as("TASpecialist")


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "scoring_model.v1.yaml",
                    tmp_path / "signals" / "scoring_model.v1.yaml")
    (tmp_path / "jds").mkdir()
    # A screenable req owned by the TA persona: JD + benign rubric + one résumé.
    (tmp_path / "jds" / f"{STEM}.md").write_text("# Finance Analyst\n\nOwns treasury.",
                                                 encoding="utf-8")
    save_req_meta(tmp_path / "jds" / f"{STEM}.md", icims_req_id="REQ-1",
                  job_location="IN", status="active", created_by=TA_EMAIL)
    (tmp_path / "jds" / f"{STEM}.rubric.v1.yaml").write_text(
        "role_title: Finance Analyst\njd_ref: jds/" + STEM + ".md\nversion: 1\n"
        "requirements:\n"
        "  - {id: r01, text: GL accounting, type: skill, kind: must_have, weight: 1}\n",
        encoding="utf-8")
    resumes = tmp_path / "data/roles" / STEM / "resumes"
    resumes.mkdir(parents=True)
    (resumes / "resume-0.pdf").write_bytes(b"pdf-lock-0")
    monkeypatch.chdir(tmp_path)

    fresh = jobs._MemoryStore()
    monkeypatch.setattr(jobs, "_MEMORY", fresh)
    jobs._reset_live_state()
    yield fresh
    jobs._reset_live_state()


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _lock(store, status="running", actor=TA_EMAIL, job_id="jrun"):
    store.create(job_id, STEM)
    store.save(job_id, STEM, {"status": status, "done": 1, "total": 3,
                              "message": "…", "error": ""})
    jobs._live_update(job_id, actor=actor)
    return job_id


# --- the batch freeze -----------------------------------------------------------------


@pytest.mark.parametrize("status", ["running", "paused"])
def test_batch_mutations_409_while_locked(client, workspace, status):
    _lock(workspace, status=status)
    h = _as("TASpecialist")  # even the initiator can't change the batch mid-run

    up = client.post(f"/api/roles/{STEM}/batch/upload", headers=h,
                     files=[("files", ("new.pdf", b"pdf-new", "application/pdf"))])
    imp = client.post(f"/api/roles/{STEM}/batch/import", headers=h, json={"folder": "x"})
    rem = client.delete(f"/api/roles/{STEM}/batch/resume-0.pdf", headers=h)
    clr = client.delete(f"/api/roles/{STEM}/batch", headers=h)
    for resp in (up, imp, rem, clr):
        assert resp.status_code == 409, resp.text
        assert "locked" in resp.json()["detail"]
    # nothing changed on disk
    assert (Path("data/roles") / STEM / "resumes" / "resume-0.pdf").exists()
    assert not (Path("data/roles") / STEM / "resumes" / "new.pdf").exists()


def test_batch_unlocks_after_cancel(client, workspace):
    job_id = _lock(workspace, status="paused")
    assert client.delete(f"/api/roles/{STEM}/batch/resume-0.pdf",
                         headers=_as("TASpecialist")).status_code == 409

    r = client.post(f"/api/screen/jobs/{job_id}/cancel", headers=_as("TASpecialist"),
                    json={"intent": "cancel"})
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    assert client.delete(f"/api/roles/{STEM}/batch/resume-0.pdf",
                         headers=_as("TASpecialist")).status_code == 200


# --- who may start / control a run ------------------------------------------------------


def test_start_is_409_for_others_and_idempotent_for_the_initiator(client, workspace):
    _lock(workspace, status="running")

    # another leader (recruiter, but neither initiator nor Super Admin) → 409
    r = client.post(f"/api/roles/{STEM}/screen", headers=_as("HRHead"))
    assert r.status_code == 409
    assert TA_EMAIL in r.json()["detail"] and "Super Admin" in r.json()["detail"]

    # the initiator gets the existing run back (idempotent, no second spawn)
    r = client.post(f"/api/roles/{STEM}/screen", headers=_as("TASpecialist"))
    assert r.status_code == 200 and r.json()["job_id"] == "jrun"

    # a Super Admin may act on any run
    r = client.post(f"/api/roles/{STEM}/screen", headers=_as("SuperAdmin"))
    assert r.status_code == 200 and r.json()["job_id"] == "jrun"


def test_paused_lock_blocks_other_starters_with_a_plain_message(client, workspace):
    _lock(workspace, status="paused")
    r = client.post(f"/api/roles/{STEM}/screen", headers=_as("HRHead"))
    assert r.status_code == 409 and "paused" in r.json()["detail"]


def test_cancel_route_is_initiator_or_superadmin_only(client, workspace):
    job_id = _lock(workspace, status="paused")

    r = client.post(f"/api/screen/jobs/{job_id}/cancel", headers=_as("HRHead"),
                    json={"intent": "cancel"})
    assert r.status_code == 403 and TA_EMAIL in r.json()["detail"]

    # a Super Admin can release someone else's run
    r = client.post(f"/api/screen/jobs/{job_id}/cancel", headers=_as("SuperAdmin"),
                    json={"intent": "cancel"})
    assert r.status_code == 200 and r.json()["status"] == "stopped"


def test_cancel_route_validates_the_intent(client, workspace):
    job_id = _lock(workspace, status="running")
    r = client.post(f"/api/screen/jobs/{job_id}/cancel", headers=_as("TASpecialist"),
                    json={"intent": "resume"})
    assert r.status_code == 400 and "pause" in r.json()["detail"].lower()


# --- the lock on role payloads -----------------------------------------------------------


def test_role_payload_carries_the_lock(client, workspace):
    detail = client.get(f"/api/roles/{STEM}", headers=_as("TASpecialist")).json()
    assert detail["screening"] is None

    _lock(workspace, status="paused")
    detail = client.get(f"/api/roles/{STEM}", headers=_as("TASpecialist")).json()
    assert detail["screening"]["status"] == "paused"
    assert detail["screening"]["started_by"] == TA_EMAIL
