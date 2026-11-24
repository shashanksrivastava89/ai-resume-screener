"""P39: the event-sourced preference store + the capability-guarded API.

Offline: a tmp_path workspace (chdir so signals/*.jsonl land there) + the
TestClient/_as() pattern. Time is frozen via monkeypatch on preferences._now so
the 6-month re-review expiry is deterministic.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import resume_screener.preferences as P
from resume_screener.events import load_events
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "signals").mkdir()
    (tmp_path / "policies").mkdir()  # empty → pack_exclusion union is empty
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _freeze(monkeypatch, iso: str):
    monkeypatch.setattr(P, "_now", lambda: iso)


# --- store lifecycle -------------------------------------------------------------------


def test_ok_pref_holds_for_sign_off(workspace):
    """Pen-test RS-01 (2026-10-16): a clean verdict no longer goes live by itself —
    the checks cannot know every disguise, so a person signs every preference off."""
    rec = P.add_preference("ta@x", "prefers strong US GAAP experience", actor_role="TASpecialist")
    assert rec["status"] == "pending" and rec["cap_points"] == 2.0
    assert rec["classification"]["level"] == "ok"
    assert not P.applies(P.get_pref(rec["pref_id"]))
    [approved] = P.approve([rec["pref_id"]], approver="head@x", approver_role="HRHead")
    assert approved["status"] == "approved" and P.applies(P.get_pref(rec["pref_id"]))


def test_a_preference_stored_active_before_the_change_waits_again(workspace):
    """Lines written as 'active' (live with nobody looking) read as pending: they stop
    applying until someone approves them, and they join the queue. No migration."""
    import json

    Path("signals/preferences.jsonl").write_text(json.dumps({
        "pref_id": "legacy1", "owner": "ta@x", "text": "prefers strong Excel modelling",
        "action": "add", "status": "active", "classification": {"level": "ok"},
        "cap_points": 2.0, "approved_by": None, "approved_at": None, "review_due": None,
        "actor": "ta@x", "ts": "2026-09-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    rec = P.get_pref("legacy1")
    assert rec["display_status"] == "pending" and not P.applies(rec)
    assert [p["pref_id"] for p in P.list_prefs() if p["display_status"] == "pending"] == ["legacy1"]
    P.approve(["legacy1"], approver="head@x")
    assert P.applies(P.get_pref("legacy1"))


def test_nobody_approves_their_own_preference(workspace):
    rec = P.add_preference("head@x", "prefers strong US GAAP experience", actor_role="HRHead")
    assert P.approve([rec["pref_id"]], approver="HEAD@x") == []  # case-insensitive
    assert P.get_pref(rec["pref_id"])["display_status"] == "pending"
    assert P.own_pref_ids([rec["pref_id"], "other"], "head@x") == [rec["pref_id"]]


def test_warn_pref_pending_then_approve_then_rereview(workspace, monkeypatch):
    _freeze(monkeypatch, "2026-01-01T00:00:00+00:00")
    rec = P.add_preference("ta@x", "prefers candidates in Mumbai", actor_role="TASpecialist")
    assert rec["status"] == "pending"

    [approved] = P.approve([rec["pref_id"]], approver="head@x", approver_role="HRHead")
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "head@x"
    assert approved["review_due"] == P._add_months("2026-01-01T00:00:00+00:00", 6)

    # still fresh at 3 months
    _freeze(monkeypatch, "2026-04-01T00:00:00+00:00")
    assert P.get_pref(rec["pref_id"])["display_status"] == "approved"
    # past the 6-month window → suspended for re-review
    _freeze(monkeypatch, "2026-09-01T00:00:00+00:00")
    assert P.get_pref(rec["pref_id"])["display_status"] == "rereview"


def test_blocked_pref_has_no_approval_path(workspace):
    rec = P.add_preference("ta@x", "prefers candidates under 30", actor_role="TASpecialist")
    assert rec["status"] == "blocked"
    assert rec["pref_id"] in {p["pref_id"] for p in P.list_prefs()}  # the attempt is on record
    assert P.approve([rec["pref_id"]], approver="head@x") == []  # skipped, not approved
    assert P.get_pref(rec["pref_id"])["status"] == "blocked"


def test_withdraw_own_only(workspace):
    rec = P.add_preference("ta@x", "strong treasury background", actor_role="TASpecialist")
    assert P.withdraw(rec["pref_id"], owner="someone-else@x") is None  # not theirs
    out = P.withdraw(rec["pref_id"], owner="ta@x")
    assert out["status"] == "withdrawn"


def test_bulk_approve(workspace):
    a = P.add_preference("ta@x", "prefers candidates in Mumbai")
    b = P.add_preference("hr@x", "prefers candidates from tier-1 colleges")
    approved = P.approve([a["pref_id"], b["pref_id"]], approver="head@x", approver_role="HRHead")
    assert {r["pref_id"] for r in approved} == {a["pref_id"], b["pref_id"]}
    assert all(r["status"] == "approved" for r in approved)


def test_events_emitted_on_transitions(workspace):
    rec = P.add_preference("ta@x", "prefers candidates in Mumbai")
    assert load_events(kinds=["pref_add"])
    assert load_events(kinds=["pref_review_requested"])  # warn → HR Head notify
    P.approve([rec["pref_id"]], approver="head@x")
    assert load_events(kinds=["pref_approve"])


def test_list_scoping_and_atomicity_smoke(workspace):
    for i in range(5):
        P.add_preference("ta@x", f"strong skill number {i}")
    P.add_preference("other@x", "strong ledger skills")
    mine = P.list_prefs(owner="ta@x")
    assert len(mine) == 5 and all(p["owner"] == "ta@x" for p in mine)
    assert len(P.list_prefs()) == 6  # everyone


# --- API capability guards -------------------------------------------------------------


def test_prefs_own_can_list_and_add(client):
    assert client.get("/api/preferences", headers=_as("TASpecialist")).status_code == 200
    r = client.post("/api/preferences", headers=_as("TASpecialist"),
                    json={"text": "prefers strong US GAAP experience"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert r.json()["status_label"] == "Waiting for HR Head sign-off"


def test_check_is_dry_run(client):
    r = client.post("/api/preferences/check", headers=_as("TASpecialist"),
                    json={"text": "prefers candidates in Mumbai"})
    assert r.status_code == 200 and r.json()["level"] == "warn"
    # nothing was stored
    assert client.get("/api/preferences", headers=_as("TASpecialist")).json()["preferences"] == []


def test_pending_queue_requires_prefs_approve(client):
    # a warn pref from a TA specialist
    client.post("/api/preferences", headers=_as("TASpecialist"),
                json={"text": "prefers candidates in Mumbai"})
    # TA specialist cannot see the queue
    assert client.get("/api/preferences/pending", headers=_as("TASpecialist")).status_code == 403
    # HR Head can, and sees the one pending item
    q = client.get("/api/preferences/pending", headers=_as("HRHead"))
    assert q.status_code == 200 and q.json()["count"] == 1


def test_approve_flow_and_me_badge(client):
    add = client.post("/api/preferences", headers=_as("TASpecialist"),
                      json={"text": "prefers candidates in Mumbai"}).json()
    # HR Head badge shows 1 pending
    assert client.get("/api/me", headers=_as("HRHead")).json()["pref_pending"] == 1
    ok = client.post("/api/preferences/approve", headers=_as("HRHead"),
                     json={"pref_ids": [add["id"]]})
    assert ok.status_code == 200 and ok.json()["approved"] == 1
    # badge clears
    assert client.get("/api/me", headers=_as("HRHead")).json()["pref_pending"] == 0


def test_an_approver_cannot_sign_off_their_own_preference(client):
    own = client.post("/api/preferences", headers=_as("HRHead"),
                      json={"text": "prefers strong US GAAP experience"}).json()
    # it is in the queue, but not counted as work for its owner
    assert client.get("/api/me", headers=_as("HRHead")).json()["pref_pending"] == 0
    assert client.get("/api/me", headers=_as("SuperAdmin")).json()["pref_pending"] == 1
    r = client.post("/api/preferences/approve", headers=_as("HRHead"),
                    json={"pref_ids": [own["id"]]})
    assert r.status_code == 403 and "your own preference" in r.json()["detail"]
    r = client.post("/api/preferences/approve", headers=_as("SuperAdmin"),
                    json={"pref_ids": [own["id"]]})
    assert r.status_code == 200 and r.json()["approved"] == 1


def test_superadmin_holds_prefs_own(client):
    # 2026-07-19: the SuperAdmin is the superset — own preferences included.
    assert client.get("/api/preferences", headers=_as("SuperAdmin")).status_code == 200


def test_decline_requires_prefs_approve_and_notes(client):
    add = client.post("/api/preferences", headers=_as("TASpecialist"),
                      json={"text": "prefers candidates in Mumbai"}).json()
    assert client.post(f"/api/preferences/{add['id']}/decline", headers=_as("TASpecialist"),
                       json={"note": "no"}).status_code == 403
    r = client.post(f"/api/preferences/{add['id']}/decline", headers=_as("HRHead"),
                    json={"note": "not job-related enough"})
    assert r.status_code == 200 and r.json()["status"] == "declined"
