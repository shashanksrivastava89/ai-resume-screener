"""P41: hiring-manager takes + the HM access model.

Takes are INPUT — they must NEVER reach decisions.jsonl. HMs are scoped to the
requisitions assigned to them, can't decide/screen/manage batches, and the whole
surface is dark unless FEATURE_HM_PORTAL is on. Offline: tmp_path workspace +
TestClient/_as().
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener import hm_takes
from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import load_events
from resume_screener.models import candidate_id_for
from tests.test_sso_identity import _as

def _portal(on: bool) -> None:
    """Turn the hiring-manager portal on/off the way the app does (2026-09-07: it is
    an ordinary flag a SuperAdmin toggles, not FEATURE_HM_PORTAL any more)."""
    from resume_screener import feature_flags

    feature_flags.set_flag("hm_portal", None, on, author="sa@example.com")


REPO_ROOT = Path(__file__).resolve().parents[1]
HM_EMAIL = "hiringmanager@example.com"  # matches _as("HiringManager")


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
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml", tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    # AFTER the chdir, and after the features.yaml copy above: hm_portal became an
    # ordinary file-backed flag (2026-09-07) and set_flag resolves signals/ against
    # the CWD, so called any earlier this wrote the REPOSITORY'S features.yaml and
    # appended to its events.jsonl and audit_log.jsonl.
    _portal(True)  # portal ON by default
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _mk_role(stem="finance-analyst", title="Finance Analyst", hm=HM_EMAIL, cids=("c1", "c2")):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text(f"# {title}\n\nOwns treasury.", encoding="utf-8")
    meta = {"icims_req_id": f"REQ-{stem}", "job_location": "IN", "status": "active",
            "created_by": "taspecialist@example.com"}
    if hm:
        meta["hm"] = {"email": hm, "name": "Rahul", "status": "invited"}
    save_req_meta(jd, **meta)
    # a screened run so takes have candidates to reference
    out = Path("output") / stem
    (out / "candidates").mkdir(parents=True, exist_ok=True)
    for cid in cids:
        (out / "candidates" / f"{cid}.json").write_text(json.dumps(
            {"score": {"candidate_id": cid, "fit_band": "Strong", "final_score": 82.0}}),
            encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps(
        {"run_id": "run-1", "counts": {"scored": len(cids)}}), encoding="utf-8")
    return stem


# --- store: recording, supersede, decisions.jsonl untouched -------------------------


def test_takes_recorded_and_retake_supersedes(client):
    stem = _mk_role()
    r = client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                    json={"takes": [{"candidate_id": "c1", "take": "yes", "note": "solid"}]})
    assert r.status_code == 200 and r.json()["recorded"] == 1
    # a re-take on the same (run, candidate, hm) supersedes
    client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                json={"takes": [{"candidate_id": "c1", "take": "strong_yes"}]})
    latest = hm_takes.takes_by_candidate(stem, "run-1")
    assert latest["c1"]["take"] == "strong_yes"  # newest wins
    assert latest["c1"]["hm_email"] == HM_EMAIL


def test_bulk_takes(client):
    stem = _mk_role()
    r = client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                    json={"takes": [{"candidate_id": "c1", "take": "yes"},
                                    {"candidate_id": "c2", "take": "no"}]})
    assert r.json()["recorded"] == 2


def test_bad_take_value_and_unknown_candidate_rejected(client):
    stem = _mk_role()
    assert client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                       json={"takes": [{"candidate_id": "c1", "take": "maybe"}]}).status_code == 400
    assert client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                       json={"takes": [{"candidate_id": "ghost", "take": "yes"}]}).status_code == 404


def test_takes_never_touch_decisions_log(client):
    stem = _mk_role()
    client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                json={"takes": [{"candidate_id": "c1", "take": "strong_yes"}]})
    client.post(f"/api/roles/{stem}/takes/send", headers=_as("HiringManager"))
    # the human-decision log must not exist — a take is input, never a decision
    assert not Path("output/feedback/decisions.jsonl").exists()
    assert Path("output/feedback/hm_takes.jsonl").exists()


def test_send_emits_event(client):
    stem = _mk_role()
    client.post(f"/api/roles/{stem}/takes/send", headers=_as("HiringManager"))
    assert load_events(req_id=stem, kinds=["hm_takes_sent"])


# --- access model -------------------------------------------------------------------


def test_hm_cannot_decide_screen_or_manage_batch(client):
    stem = _mk_role()
    hm = _as("HiringManager")
    assert client.post(f"/api/roles/{stem}/decisions", headers=hm,
                       json={"candidate_id": "c1", "action": "forward"}).status_code == 403
    assert client.post(f"/api/roles/{stem}/screen", headers=hm).status_code == 403
    assert client.get(f"/api/roles/{stem}/batch", headers=hm).status_code == 403


def test_hm_403_on_unassigned_req(client):
    stem = _mk_role(hm="someone.else@example.com")  # assigned to a DIFFERENT HM
    hm = _as("HiringManager")
    assert client.post(f"/api/roles/{stem}/takes", headers=hm,
                       json={"takes": [{"candidate_id": "c1", "take": "yes"}]}).status_code == 403
    assert client.get(f"/api/roles/{stem}", headers=hm).status_code == 403


def test_hm_list_shows_only_assigned(client):
    mine = _mk_role(stem="mine", title="Mine", hm=HM_EMAIL)
    _mk_role(stem="theirs", title="Theirs", hm="other@example.com")
    roles = client.get("/api/roles", headers=_as("HiringManager")).json()["roles"]
    assert {r["req_id"] for r in roles} == {mine}


def test_hm_can_view_assigned_review(client):
    stem = _mk_role()
    assert client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).status_code == 200


def test_resume_file_stays_recruiter_only(client):
    stem = _mk_role()
    # HM sees evidence, not the original file
    assert client.get(f"/api/roles/{stem}/candidates/c1/resume",
                      headers=_as("HiringManager")).status_code == 403


def test_review_card_carries_hm_take(client):
    # Review reconciles scored records against the batch (content-hash ids), so this
    # role needs a real attached résumé whose hash IS the candidate id.
    stem = "with-batch"
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Analyst\n\nOwns treasury.", encoding="utf-8")
    save_req_meta(jd, icims_req_id="REQ-wb", job_location="IN", status="active",
                  created_by="taspecialist@example.com",
                  hm={"email": HM_EMAIL, "name": "Rahul", "status": "invited"})
    folder = Path(client.get(f"/api/roles/{stem}/batch",
                             headers=_as("TASpecialist")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    data = b"Anita Rao resume bytes"
    (folder / "Anita Rao.pdf").write_bytes(data)
    cid = candidate_id_for(data)
    out = Path("output") / stem
    (out / "candidates").mkdir(parents=True)
    (out / "candidates" / f"{cid}.json").write_text(json.dumps({
        "score": {"candidate_id": cid, "role_title": "Analyst", "jurisdiction": "IN",
                  "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                             "must_haves_missing": [], "coverage_score": 0.82,
                             "base_score": 82.0, "eligible": True, "reasons": []},
                  "layer2": None, "critic": None, "final_score": 82.0,
                  "fit_band": "Strong", "confidence": "high", "summary_rationale": "ok"},
        "findings": {"candidate_id": cid, "role_title": "Analyst", "rubric_version": 1,
                     "findings": []},
        "suspended_adjustments": [],
    }), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps(
        {"run_id": "run-1", "counts": {"scored": 1}}), encoding="utf-8")

    client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"),
                json={"takes": [{"candidate_id": cid, "take": "strong_yes"}]})
    payload = client.get(f"/api/roles/{stem}/review", headers=_as("TASpecialist")).json()
    assert payload["ready"] is True
    card = payload["cards"][0]
    assert card["hm_take"] == {"take": "strong_yes", "hm_name": "Rahul"}


def test_metrics_awaiting_hm_counts_assigned(client):
    _mk_role(stem="withhm", hm=HM_EMAIL)
    _mk_role(stem="nohm", hm=None)
    metrics = client.get("/api/roles", headers=_as("TASpecialist")).json()["metrics"]
    assert metrics["awaiting_hm"] == 1


# --- the flag gate ------------------------------------------------------------------


def test_everything_403_when_flag_off(client, monkeypatch):
    stem = _mk_role()
    _portal(False)
    hm = _as("HiringManager")
    # HM loses hm_take (capability suppression) -> even the list 403s
    assert client.get("/api/roles", headers=hm).status_code == 403
    assert client.post(f"/api/roles/{stem}/takes", headers=hm,
                       json={"takes": [{"candidate_id": "c1", "take": "yes"}]}).status_code == 403
    # recruiter-initiated HM routes are dark too (defense in depth)
    assert client.put(f"/api/roles/{stem}/hm", headers=_as("TASpecialist"),
                      json={"email": HM_EMAIL, "name": "R"}).status_code == 403
    assert client.get(f"/api/roles/{stem}/activity", headers=_as("TASpecialist")).status_code == 403
