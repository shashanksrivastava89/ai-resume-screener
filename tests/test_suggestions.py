"""P41: hiring-manager checklist suggestions — thread lifecycle + access model.

HM proposes (INPUT), recruiter accepts/declines (and only then publishes the
rubric). Latest line per suggestion_id wins. Offline: tmp_path + TestClient/_as().
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import suggestions
from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import load_events
from tests.test_sso_identity import _as

def _portal(on: bool) -> None:
    """Turn the hiring-manager portal on/off the way the app does (2026-09-07: it is
    an ordinary flag a SuperAdmin toggles, not FEATURE_HM_PORTAL any more)."""
    from resume_screener import feature_flags

    feature_flags.set_flag("hm_portal", None, on, author="sa@example.com")


REPO_ROOT = Path(__file__).resolve().parents[1]
HM_EMAIL = "hiringmanager@example.com"


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
    _portal(True)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _mk_role(stem="finance-analyst", hm=HM_EMAIL):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    meta = {"icims_req_id": f"REQ-{stem}", "job_location": "IN", "status": "active",
            "created_by": "taspecialist@example.com"}
    if hm:
        meta["hm"] = {"email": hm, "name": "Rahul", "status": "invited"}
    save_req_meta(jd, **meta)
    return stem


def _suggest(client, stem, **body):
    payload = {"requirement_id": "r02", "old_text": "Oracle EBS",
               "new_text": "Oracle Fusion", "note": "we moved to Fusion"}
    payload.update(body)
    return client.post(f"/api/roles/{stem}/suggestions", headers=_as("HiringManager"), json=payload)


# --- lifecycle ----------------------------------------------------------------------


def test_hm_creates_then_recruiter_accepts(client):
    stem = _mk_role()
    created = _suggest(client, stem)
    assert created.status_code == 200
    sid = created.json()["suggestion_id"]
    assert created.json()["status"] == "open"

    acc = client.post(f"/api/suggestions/{sid}/accept", headers=_as("TASpecialist"),
                      json={})
    assert acc.status_code == 200
    body = acc.json()
    assert body["status"] == "accepted" and body["merged_text"] == "Oracle Fusion"
    assert body["requirement_id"] == "r02"
    # events recorded for both ends
    assert load_events(req_id=stem, kinds=["suggestion_created"])
    assert load_events(req_id=stem, kinds=["suggestion_accepted"])


def test_accept_with_edited_text_merges_the_edit(client):
    stem = _mk_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    acc = client.post(f"/api/suggestions/{sid}/accept", headers=_as("TASpecialist"),
                      json={"edited_text": "Oracle Fusion Cloud ERP"})
    assert acc.json()["merged_text"] == "Oracle Fusion Cloud ERP"


def test_decline_records_and_notifies(client):
    stem = _mk_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    dec = client.post(f"/api/suggestions/{sid}/decline", headers=_as("TASpecialist"),
                      json={"note": "keep EBS for now"})
    assert dec.status_code == 200 and dec.json()["status"] == "declined"
    assert load_events(req_id=stem, kinds=["suggestion_declined"])


def test_latest_transition_wins_and_second_decide_400s(client):
    stem = _mk_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    assert client.post(f"/api/suggestions/{sid}/accept", headers=_as("TASpecialist"), json={}).status_code == 200
    # deciding again is rejected (already decided) — the folded status is terminal
    assert client.post(f"/api/suggestions/{sid}/decline", headers=_as("TASpecialist"), json={}).status_code == 400
    jd = Path("jds") / f"{stem}.md"
    assert suggestions.get_suggestion(jd, sid)["status"] == "accepted"


# --- listing (P42 SuggestionCard feed) ----------------------------------------------


def test_list_suggestions_returns_decided_with_status(client):
    """2026-07-19: GET returns EVERY status (the HM finally sees outcomes +
    notes); clients filter — the TA's cards use only status == "open"."""
    stem = _mk_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    _suggest(client, stem, requirement_id="r03", new_text="Add IFRS")  # a second, stays open
    client.post(f"/api/suggestions/{sid}/decline", headers=_as("TASpecialist"), json={})
    listed = client.get(f"/api/roles/{stem}/suggestions", headers=_as("TASpecialist")).json()["suggestions"]
    by_status = {s["status"]: s["requirement_id"] for s in listed}
    assert by_status == {"open": "r03", "declined": "r02"}


def test_hm_lists_only_own_assigned_suggestions(client):
    stem = _mk_role()
    _suggest(client, stem)
    listed = client.get(f"/api/roles/{stem}/suggestions", headers=_as("HiringManager")).json()["suggestions"]
    assert len(listed) == 1 and listed[0]["actor"] == HM_EMAIL


def test_list_suggestions_dark_when_flag_off(client, monkeypatch):
    stem = _mk_role()
    _suggest(client, stem)
    _portal(False)
    assert client.get(f"/api/roles/{stem}/suggestions",
                      headers=_as("TASpecialist")).status_code == 403


# --- access model -------------------------------------------------------------------


def test_recruiter_cannot_create_suggestion(client):
    stem = _mk_role()
    # create requires hm_take (assigned HM) — a recruiter has no hm_take
    assert client.post(f"/api/roles/{stem}/suggestions", headers=_as("TASpecialist"),
                       json={"requirement_id": "r02", "new_text": "x"}).status_code == 403


def test_hm_cannot_decide_suggestion(client):
    stem = _mk_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    # accept/decline are recruiter-only
    assert client.post(f"/api/suggestions/{sid}/accept", headers=_as("HiringManager"), json={}).status_code == 403


def test_non_assigned_hm_cannot_suggest(client):
    stem = _mk_role(hm="someone.else@example.com")
    assert _suggest(client, stem).status_code == 403


def test_suggestions_dark_when_flag_off(client, monkeypatch):
    stem = _mk_role()
    _portal(False)
    assert _suggest(client, stem).status_code == 403
    # and the request-hm-review route (recruiter) is dark too
    assert client.post(f"/api/roles/{stem}/request-hm-review",
                       headers=_as("TASpecialist")).status_code == 403
