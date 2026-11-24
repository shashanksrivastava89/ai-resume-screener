"""P43: the checklist critic + approval state.

Requirement TEXT runs the same deterministic critic as HR preferences (P39).
Blocked text can never be saved (400, no version bump, no file); warned text is
saved but flagged and routed through HR-Head approval; clean text publishes
exactly as before. Offline: tmp_path workspace + TestClient/_as().
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener.events import load_events
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


def _mk_role(client) -> str:
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9100",
    })
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


CLEAN = [
    {"text": "Strong US GAAP experience", "kind": "must_have", "importance": 5},
    {"text": "Oracle EBS exposure", "kind": "nice_to_have", "importance": 2},
]
WARN_ROW = {"text": "prefers candidates from tier-1 colleges", "kind": "nice_to_have", "importance": 2}
BLOCKED_ROW = {"text": "no disabled candidates", "kind": "must_have", "importance": 4}


def test_each_route_checks_at_its_own_stage(client, monkeypatch):
    """A topic can be enforced for preferences and not the checklist, or the other
    way round — so every route must say which stage it is (2026-10-16)."""
    from resume_screener import prefcheck, prefcheck_semantic

    seen: list[str] = []
    real = prefcheck.classify_text

    def spy(text, **kw):
        seen.append(kw.get("stage", "<default>"))
        return real(text, **kw)

    monkeypatch.setattr(prefcheck, "classify_text", spy)
    monkeypatch.setattr(prefcheck_semantic, "classify_text", spy)
    req_id = _mk_role(client)
    client.post(f"/api/roles/{req_id}/checklist/check", headers=_as("TASpecialist"),
                json={"text": "Strong US GAAP experience"})
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})
    client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"))
    assert seen and set(seen) == {"checklist"}

    seen.clear()
    client.post("/api/preferences/check", headers=_as("TASpecialist"),
                json={"text": "prefers strong US GAAP experience"})
    client.post("/api/preferences", headers=_as("TASpecialist"),
                json={"text": "prefers strong US GAAP experience"})
    assert seen and set(seen) == {"preferences"}


def test_a_topic_applies_only_at_its_stages():
    from resume_screener.prefcheck import classify_text
    from resume_screener.scoring_model import TopicRule

    prefs_only = [TopicRule(id="t", name="T", reason="r", stages=["preferences"],
                            phrases=["zebra stripes"])]
    assert classify_text("zebra stripes", topics=prefs_only, stage="preferences").level == "warn"
    assert classify_text("zebra stripes", topics=prefs_only, stage="checklist").level == "ok"


def _rubric_files():
    return sorted(Path("jds").glob("*.rubric.v*.yaml"))


# --- blocked ------------------------------------------------------------------------


def test_blocked_row_400s_and_writes_no_version(client):
    req_id = _mk_role(client)
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": [*CLEAN, BLOCKED_ROW]})
    assert r.status_code == 400
    # 2026-09-07: it states the rule, in the checklist's own noun ("Requirements",
    # not the pack's "Preferences"), and never suggests a rewording that would carry
    # the same intent past it.
    assert "This requirement can't be used." in r.json()["detail"]
    assert "Requirements can never be based on it." in r.json()["detail"]
    assert "reword" not in r.json()["detail"].lower()
    assert "can never be based on" in r.json()["detail"]
    # NOTHING saved — no rubric file, GET still reports no checklist
    assert _rubric_files() == []
    assert client.get(f"/api/roles/{req_id}/rubric",
                      headers=_as("TASpecialist")).json()["version"] is None


# --- clean (regression) -------------------------------------------------------------


def test_clean_publishes_exactly_as_before(client):
    req_id = _mk_role(client)
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": CLEAN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["needs_approval"] is False
    assert "approval" not in body
    assert all(row["critic_level"] is None for row in body["requirements"])
    # GET carries no approval block
    assert "approval" not in client.get(f"/api/roles/{req_id}/rubric",
                                        headers=_as("TASpecialist")).json()


def test_checklist_publish_event_emitted(client):
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": CLEAN})
    events = load_events(req_id=req_id, kinds=["checklist_publish"])
    assert events and events[-1]["summary"] == "Checklist v1 published"


# --- warn + approval flow -----------------------------------------------------------


def test_warn_row_flagged_saved_and_needs_approval(client):
    req_id = _mk_role(client)
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": [*CLEAN, WARN_ROW]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["needs_approval"] is True
    warned = [row for row in body["requirements"] if row["critic_level"] == "warn"]
    assert len(warned) == 1 and warned[0]["critic_reason"]  # plain reason shown verbatim
    # the version WAS saved (flagged, not blocked)
    assert body["version"] == 1 and _rubric_files()
    # GET carries the approval block, status "none" (not yet requested)
    got = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist")).json()
    assert got["approval"]["status"] == "none"
    assert got["approval"]["flagged_requirement_ids"] == [warned[0]["id"]]


def test_request_then_approve_flow(client):
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})

    # recruiter requests → pending + event
    reqd = client.post(f"/api/roles/{req_id}/checklist-approval/request", headers=_as("TASpecialist"))
    assert reqd.status_code == 200 and reqd.json()["approval"]["status"] == "pending"
    assert load_events(req_id=req_id, kinds=["checklist_approval_requested"])
    assert client.get(f"/api/roles/{req_id}/rubric",
                      headers=_as("TASpecialist")).json()["approval"]["status"] == "pending"

    # a recruiter CANNOT approve — rules_edit only, exact message
    denied = client.post(f"/api/roles/{req_id}/checklist-approval/approve", headers=_as("TASpecialist"))
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Approving flagged checklists is limited to the HR Head."

    # HR Head approves → approved + event; GET no longer gates
    ok = client.post(f"/api/roles/{req_id}/checklist-approval/approve", headers=_as("HRHead"))
    assert ok.status_code == 200 and ok.json()["approval"]["status"] == "approved"
    assert load_events(req_id=req_id, kinds=["checklist_approved"])
    got = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist")).json()
    assert "approval" not in got  # approved → unblocked
    # the flag stays visible for transparency even after approval
    assert any(row["critic_level"] == "warn" for row in got["requirements"])


def test_new_version_reopens_approval(client):
    req_id = _mk_role(client)
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
               json={"requirements": [*CLEAN, WARN_ROW]})
    client.post(f"/api/roles/{req_id}/checklist-approval/request", headers=_as("TASpecialist"))
    client.post(f"/api/roles/{req_id}/checklist-approval/approve", headers=_as("HRHead"))
    # re-publishing a still-flagged checklist resets approval to "none" (fresh sign-off)
    body = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                      json={"requirements": [*CLEAN, WARN_ROW]}).json()
    assert body["version"] == 2 and body["needs_approval"] is True
    got = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist")).json()
    assert got["approval"]["status"] == "none" and got["approval"]["version"] == 2


# --- the add-row dry-run (the design's "Check & add") -------------------------------


def test_checklist_check_dry_runs_without_writing(client):
    req_id = _mk_role(client)
    hdrs = _as("TASpecialist")

    ok = client.post(f"/api/roles/{req_id}/checklist/check", headers=hdrs,
                     json={"text": CLEAN[0]["text"]})
    assert ok.status_code == 200 and ok.json()["level"] == "ok"

    warn = client.post(f"/api/roles/{req_id}/checklist/check", headers=hdrs,
                       json={"text": WARN_ROW["text"]})
    assert warn.status_code == 200
    assert warn.json()["level"] == "warn" and warn.json()["reason"]

    blocked = client.post(f"/api/roles/{req_id}/checklist/check", headers=hdrs,
                          json={"text": BLOCKED_ROW["text"]})
    assert blocked.status_code == 200
    assert blocked.json()["level"] == "blocked" and blocked.json()["reason"]

    # a dry-run writes NOTHING — no rubric version, empty text 400s
    assert _rubric_files() == []
    assert client.post(f"/api/roles/{req_id}/checklist/check", headers=hdrs,
                       json={"text": "  "}).status_code == 400
