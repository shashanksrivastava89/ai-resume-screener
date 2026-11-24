"""The HM share flow + suggestion upgrades + notifications (2026-07-19).

The hiring manager's Review tab is EMPTY until the recruiter clicks "Share with
hiring manager" on the Shortlist tab; then they see the share's frozen SNAPSHOT
(decided candidates as the working set, Not-a-fit + undecided minimized) —
never a live sync. Later recruiter changes (decisions, comments) reach the HM
only through "Send updates", which notifies per change category. Comments are
the collaboration thread — every entry carries its OWN author. Suggestions
carry kind/importance, can be withdrawn while open, and their outcomes (with
the recruiter's note) are visible to the HM. Every outcome lands on the
/api/notifications bell. Offline: tmp_path workspace + TestClient/_as().
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import load_events
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]
HM_EMAIL = "hiringmanager@example.com"  # matches _as("HiringManager")
TA_EMAIL = "taspecialist@example.com"   # matches _as("TASpecialist")


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.setenv("FEATURE_HM_PORTAL", "1")
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    shutil.copyfile(REPO_ROOT / "signals" / "scoring_model.v1.yaml",
                    tmp_path / "signals" / "scoring_model.v1.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _mk_screened_role(stem="finance-analyst", n=3, hm=HM_EMAIL):
    """A req with a rubric + a finished run whose résumés are ATTACHED (so
    build_review lists them). Returns (stem, [candidate ids])."""
    from resume_screener.api import service

    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    meta = {"icims_req_id": f"REQ-{stem}", "job_location": "IN", "status": "active",
            "created_by": TA_EMAIL}
    if hm:
        meta["hm"] = {"email": hm, "name": "Rahul", "status": "invited"}
    save_req_meta(jd, **meta)
    Path("jds", f"{stem}.rubric.v1.yaml").write_text(
        "role_title: Finance Analyst\njd_ref: jds/" + stem + ".md\nversion: 1\n"
        "requirements:\n"
        "  - {id: r01, text: GL accounting, type: skill, kind: must_have, weight: 1}\n"
        "  - {id: r02, text: Oracle EBS, type: skill, kind: nice_to_have, weight: 1}\n",
        encoding="utf-8")

    resumes = Path("data/roles") / stem / "resumes"
    resumes.mkdir(parents=True)
    out = Path("output") / stem
    (out / "candidates").mkdir(parents=True)
    ids = []
    for i in range(n):
        f = resumes / f"resume-{i}.pdf"
        f.write_bytes(f"pdf-{stem}-{i}".encode())  # unique per req — ids are content hashes
        cid = service.cached_candidate_id(f)
        ids.append(cid)
        (out / "candidates" / f"{cid}.json").write_text(json.dumps({
            "score": {"candidate_id": cid, "fit_band": "Possible", "final_score": 70.0,
                      "confidence": "high", "summary_rationale": "ok",
                      "layer1": {"candidate_id": cid, "rubric_version": 1,
                                 "gate_flags": [], "must_haves_missing": [],
                                 "coverage_score": 0.7, "base_score": 70.0,
                                 "eligible": True, "reasons": []},
                      "layer2": None, "critic": None, "preference_adjustments": []},
            "findings": {"candidate_id": cid, "role_title": "Finance Analyst",
                         "rubric_version": 1, "findings": []},
            "features": None, "layer2_enforcement": [],
        }), encoding="utf-8")
    from resume_screener.cache import content_hash

    stamps = service.current_config_stamps(service.config())
    (out / "run_meta.json").write_text(json.dumps(
        {"run_id": "run-1", "jurisdiction": "IN", "counts": {"scored": n},
         "versions": {"rubric": f"{stem}.rubric.v1.yaml (v1)",
                      "policy_files": {"base": stamps["policy_base"],
                                       "IN": content_hash(
                                           Path("policies/IN.yaml").read_bytes())[:12]},
                      "scoring_model": stamps["scoring_model"],
                      "preferences": {"applied": []}}}), encoding="utf-8")
    return stem, ids


def _decide(client, stem, cid, action="forward"):
    r = client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": cid, "action": action})
    assert r.status_code == 200, r.text


# --- the share gate -------------------------------------------------------------------


def test_hm_review_is_empty_until_shared_then_shows_the_snapshot(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    _decide(client, stem, ids[1], "not")

    # unshared: the HM sees an empty (but ready) review
    hm_view = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert hm_view["ready"] is True and hm_view["hm_shared"] is False
    assert hm_view["cards"] == []
    # ...and can't reach a candidate detail by guessing the id
    assert client.get(f"/api/roles/{stem}/review/{ids[0]}",
                      headers=_as("HiringManager")).status_code == 403

    # the TA shares
    r = client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    assert r.status_code == 200 and r.json()["hm_share"]["shared_by"] == TA_EMAIL
    [event] = load_events(req_id=stem, kinds=["shortlist_shared"])
    assert event["data"]["run_id"] == "run-1"
    # the event froze a decision snapshot of the whole scored batch
    snap = {row["candidate_id"]: row["action"] for row in event["data"]["snapshot"]}
    assert snap == {ids[0]: "forward", ids[1]: "not", ids[2]: None}

    # shared: the SNAPSHOT — decided candidates as the working set, plus the
    # undecided third (minimized client-side); counts on the shared set
    hm_view = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert hm_view["hm_shared"] is True
    got = {c["candidate_id"]: c["decided"] for c in hm_view["cards"]}
    assert got == {ids[0]: "forward", ids[1]: "not", ids[2]: None}
    assert hm_view["counts"]["total"] == 3
    assert hm_view["decision_counts"] == {"forward": 1, "discuss": 0, "not": 1, "awaiting": 1}
    # every shared candidate's detail opens — including the undecided one
    assert client.get(f"/api/roles/{stem}/review/{ids[0]}",
                      headers=_as("HiringManager")).status_code == 200
    assert client.get(f"/api/roles/{stem}/review/{ids[2]}",
                      headers=_as("HiringManager")).status_code == 200
    ta_view = client.get(f"/api/roles/{stem}/review", headers=_as("TASpecialist")).json()
    assert len(ta_view["cards"]) == 3 and ta_view["hm_shared"] is True


def test_share_requires_hm_and_decisions(client):
    stem, ids = _mk_screened_role(stem="no-hm-role", hm=None)
    _decide(client, stem, ids[0])
    r = client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    assert r.status_code == 400 and "hiring manager" in r.json()["detail"].lower()

    stem2, _ids2 = _mk_screened_role(stem="undecided-role")
    r = client.post(f"/api/roles/{stem2}/share-hm", headers=_as("TASpecialist"))
    assert r.status_code == 400 and "decision" in r.json()["detail"].lower()


def test_rescreen_clears_the_share(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0])
    assert client.post(f"/api/roles/{stem}/share-hm",
                       headers=_as("TASpecialist")).status_code == 200
    # a re-screen mints a new run_id → the old share no longer applies
    out = Path("output") / stem / "run_meta.json"
    meta = json.loads(out.read_text(encoding="utf-8"))
    meta["run_id"] = "run-2"
    out.write_text(json.dumps(meta), encoding="utf-8")
    hm_view = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert hm_view["hm_shared"] is False and hm_view["cards"] == []


def test_share_notifies_the_hm(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0])
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    bell = client.get("/api/notifications", headers=_as("HiringManager")).json()
    assert any(n["kind"] == "shortlist_shared" for n in bell["notifications"])
    # the TA's own bell doesn't carry it (it's addressed to the HM)
    ta_bell = client.get("/api/notifications", headers=_as("TASpecialist")).json()
    assert not any(n["kind"] == "shortlist_shared" for n in ta_bell["notifications"])


# --- live collaboration (2026-07-20): decisions + comments reach the HM live ----------


def test_decision_change_reenables_send_updates(client):
    """The HM sees decisions as a SNAPSHOT (2026-07-21): a recruiter flip after
    the share is NOT visible to the HM until 'send updates', which re-enables the
    button (hm_updates pending) and notifies the HM. (Comments stay live — that's
    a separate test.)"""
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    hm = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert {c["candidate_id"]: c["decided"] for c in hm["cards"]}[ids[0]] == "forward"

    _decide(client, stem, ids[0], "not")  # flip AFTER the share
    ta = client.get(f"/api/roles/{stem}/review", headers=_as("TASpecialist")).json()
    assert ta["hm_updates"]["pending"] is True and ta["hm_updates"]["decisions"] == [ids[0]]
    hm = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert {c["candidate_id"]: c["decided"] for c in hm["cards"]}[ids[0]] == "forward"  # still snapshot

    assert client.post(f"/api/roles/{stem}/share-hm",
                       headers=_as("TASpecialist")).status_code == 200
    hm = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert {c["candidate_id"]: c["decided"] for c in hm["cards"]}[ids[0]] == "not"
    bell = client.get("/api/notifications", headers=_as("HiringManager")).json()["notifications"]
    assert any(n["kind"] == "hm_decisions_updated" for n in bell)


def test_reshare_is_a_noop_when_nothing_new(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    assert client.post(f"/api/roles/{stem}/share-hm",
                       headers=_as("TASpecialist")).status_code == 200
    # comments + decisions are live, so re-sharing with nothing new is a friendly
    # no-op (not the old 400) — the HM already sees everything
    r = client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    assert r.status_code == 200 and r.json().get("unchanged") is True


# --- comments: the collaboration thread -------------------------------------------------


def test_comments_carry_their_own_author(client):
    """The 'Note from hrhead' bug: a note's author label was the decision's
    recorder. Comments carry their OWN author — an HR Head deciding never
    re-attributes what the TA wrote."""
    stem, ids = _mk_screened_role()
    # the HR Head records the decision; the TA writes the comment
    r = client.post(f"/api/roles/{stem}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": ids[0], "action": "forward"})
    assert r.status_code == 200
    r = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                    headers=_as("TASpecialist"), json={"text": "call them this week"})
    assert r.status_code == 200
    assert r.json()["comment"]["author"] == TA_EMAIL

    detail = client.get(f"/api/roles/{stem}/review/{ids[0]}",
                        headers=_as("TASpecialist")).json()
    [comment] = detail["comments"]
    assert comment["author"] == TA_EMAIL and comment["author_role"] == "Recruiting team"
    assert detail["decision"]["reviewer"] == "hrhead@example.com"
    # the review card counts the thread
    ta_view = client.get(f"/api/roles/{stem}/review", headers=_as("TASpecialist")).json()
    assert {c["candidate_id"]: c.get("comments") for c in ta_view["cards"]}[ids[0]] == 1
    # empty comments are refused
    assert client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                       headers=_as("TASpecialist"), json={"text": "  "}).status_code == 400


def test_comments_are_live_both_ways(client):
    """Comments are near-real-time both directions (2026-07-20): each side sees
    the other's comment immediately and is notified — no 'send updates' click."""
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")

    # before the share the HM can't comment (nothing is shared with them)
    assert client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                       headers=_as("HiringManager"), json={"text": "hi"}).status_code == 403
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))

    # the HM comments → the requisition's owner is notified right away
    r = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                    headers=_as("HiringManager"), json={"text": "I know this profile — solid."})
    assert r.status_code == 200 and r.json()["comment"]["author_role"] == "Hiring manager"
    ta_bell = client.get("/api/notifications", headers=_as("TASpecialist")).json()["notifications"]
    hit = next(n for n in ta_bell if n["kind"] == "comment_added")
    assert hit["data"]["candidate_id"] == ids[0] and hit["data"]["comment_id"]

    # the TA comments → the HM sees it IMMEDIATELY and is notified (no send)
    client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                headers=_as("TASpecialist"), json={"text": "agreed, moving forward"})
    hm_detail = client.get(f"/api/roles/{stem}/review/{ids[0]}",
                           headers=_as("HiringManager")).json()
    texts = [c["text"] for c in hm_detail["comments"]]
    assert "I know this profile — solid." in texts and "agreed, moving forward" in texts
    hm_bell = client.get("/api/notifications", headers=_as("HiringManager")).json()["notifications"]
    assert any(n["kind"] == "comment_added" for n in hm_bell)


def test_reactions_toggle_and_show_who(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    c = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                    headers=_as("TASpecialist"), json={"text": "strong"}).json()["comment"]
    cid = c["comment_id"]
    react_url = f"/api/roles/{stem}/candidates/{ids[0]}/comments/{cid}/react"

    r = client.post(react_url, headers=_as("HiringManager"), json={"emoji": "👍"})
    assert r.status_code == 200
    by_emoji = {x["emoji"]: x for x in r.json()["reactions"]}
    assert by_emoji["👍"]["count"] == 1 and by_emoji["👍"]["mine"] is True

    # the recruiter sees the HM's reaction on the thread (not theirs)
    detail = client.get(f"/api/roles/{stem}/review/{ids[0]}",
                        headers=_as("TASpecialist")).json()
    rc = next(cm for cm in detail["comments"] if cm["comment_id"] == cid)
    assert rc["reactions"][0]["emoji"] == "👍" and rc["reactions"][0]["mine"] is False
    assert "Rahul" in rc["reactions"][0]["who"]

    # toggling off removes it; an unknown emoji is refused
    assert client.post(react_url, headers=_as("HiringManager"),
                       json={"emoji": "👍"}).json()["reactions"] == []
    assert client.post(react_url, headers=_as("HiringManager"),
                       json={"emoji": "🤯"}).status_code == 400


def test_mention_notifies_the_named_person(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    parts = client.get(f"/api/roles/{stem}/participants",
                       headers=_as("TASpecialist")).json()["participants"]
    assert any(p["email"] == HM_EMAIL and p["role"] == "Hiring manager" for p in parts)

    r = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                    headers=_as("TASpecialist"),
                    json={"text": "@Rahul can you weigh in?", "mentions": [HM_EMAIL]})
    assert r.status_code == 200 and r.json()["comment"]["mentions"] == [HM_EMAIL]
    hm_bell = client.get("/api/notifications", headers=_as("HiringManager")).json()["notifications"]
    assert any(n["kind"] == "comment_mention" for n in hm_bell)


def test_batch_quality_stays_with_the_recruiter(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    ta_view = client.get(f"/api/roles/{stem}/review", headers=_as("TASpecialist")).json()
    assert ta_view["batch_quality"] is not None and ta_view["batch_quality"]["band"]
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    hm_view = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    assert hm_view["batch_quality"] is None and hm_view["hm_updates"] is None


# --- suggestions: kind/importance + withdraw + visible outcomes ------------------------


def _suggest(client, stem, **extra):
    return client.post(f"/api/roles/{stem}/suggestions", headers=_as("HiringManager"),
                       json={"requirement_id": "r02", "old_text": "Oracle EBS",
                             "new_text": "Oracle EBS or SAP", **extra})


def test_suggestion_carries_kind_and_importance_and_accept_returns_them(client):
    stem, _ids = _mk_screened_role()
    r = _suggest(client, stem, old_kind="nice_to_have", new_kind="must_have",
                 old_importance=3, new_importance=5)
    assert r.status_code == 200, r.text
    sid = r.json()["suggestion_id"]
    assert r.json()["new_kind"] == "must_have" and r.json()["new_importance"] == 5

    accepted = client.post(f"/api/suggestions/{sid}/accept",
                           headers=_as("TASpecialist"), json={})
    assert accepted.status_code == 200
    assert accepted.json()["merged_kind"] == "must_have"
    assert accepted.json()["merged_importance"] == 5

    # the HM sees the outcome (GET returns decided records now)
    listed = client.get(f"/api/roles/{stem}/suggestions",
                        headers=_as("HiringManager")).json()["suggestions"]
    assert [s["status"] for s in listed] == ["accepted"]
    bell = client.get("/api/notifications", headers=_as("HiringManager")).json()
    assert any(n["kind"] == "suggestion_accepted" for n in bell["notifications"])


def test_suggestion_rejects_a_bad_kind_or_importance(client):
    stem, _ids = _mk_screened_role()
    assert _suggest(client, stem, new_kind="deal_breaker").status_code == 400
    assert _suggest(client, stem, new_importance=9).status_code == 400


def test_withdraw_only_by_the_author_and_only_while_open(client):
    stem, _ids = _mk_screened_role()
    sid = _suggest(client, stem).json()["suggestion_id"]

    # the TA can't withdraw the HM's suggestion
    assert client.post(f"/api/suggestions/{sid}/withdraw",
                       headers=_as("TASpecialist")).status_code == 403
    # the author can
    r = client.post(f"/api/suggestions/{sid}/withdraw", headers=_as("HiringManager"))
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    # and a decided/withdrawn one can't be withdrawn again
    assert client.post(f"/api/suggestions/{sid}/withdraw",
                       headers=_as("HiringManager")).status_code == 400
    # withdrawn = no longer decidable
    assert client.post(f"/api/suggestions/{sid}/accept",
                       headers=_as("TASpecialist"), json={}).status_code == 400


def test_decline_note_reaches_the_hm(client):
    stem, _ids = _mk_screened_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    client.post(f"/api/suggestions/{sid}/decline", headers=_as("TASpecialist"),
                json={"note": "Oracle is a hard requirement from the HM's own JD."})
    [rec] = client.get(f"/api/roles/{stem}/suggestions",
                       headers=_as("HiringManager")).json()["suggestions"]
    assert rec["status"] == "declined"
    assert "hard requirement" in rec["decided_note"]


def test_hm_can_suggest_a_new_checklist_row(client):
    """The HM can propose a brand-new requirement (2026-07-20), not just edit an
    existing one — same review/approval flow. No requirement_id = a new row."""
    stem, _ids = _mk_screened_role()
    r = client.post(f"/api/roles/{stem}/suggestions", headers=_as("HiringManager"),
                    json={"new_text": "CFA charter", "new_kind": "nice_to_have",
                          "new_importance": 3})
    assert r.status_code == 200, r.text
    sid = r.json()["suggestion_id"]
    assert r.json()["requirement_id"] == ""
    ta_bell = client.get("/api/notifications", headers=_as("TASpecialist")).json()["notifications"]
    assert any("new checklist item" in n["summary"] for n in ta_bell)

    acc = client.post(f"/api/suggestions/{sid}/accept", headers=_as("TASpecialist"), json={})
    assert acc.status_code == 200
    assert acc.json()["new_row"] is True and acc.json()["merged_text"] == "CFA charter"


# --- notifications: names, req titles, admin-configurable expiry ----------------------


def test_notifications_carry_names_and_req_title(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    bell = client.get("/api/notifications", headers=_as("HiringManager")).json()
    n = next(x for x in bell["notifications"] if x["kind"] == "shortlist_shared")
    assert n["from_name"] == "Taspecialist"   # taspecialist@ → derived name
    assert n["req_title"] == "Finance Analyst"
    assert bell["expiry_days"] == 30


def test_notification_expiry_is_admin_only_and_bounded(client):
    assert client.put("/api/settings/notifications", headers=_as("TASpecialist"),
                      json={"expiry_days": 7}).status_code == 403
    r = client.put("/api/settings/notifications", headers=_as("SuperAdmin"),
                   json={"expiry_days": 7})
    assert r.status_code == 200 and r.json()["expiry_days"] == 7
    assert client.get("/api/notifications",
                      headers=_as("SuperAdmin")).json()["expiry_days"] == 7
    assert client.put("/api/settings/notifications", headers=_as("SuperAdmin"),
                      json={"expiry_days": 0}).status_code == 400


def test_expired_notifications_hidden_from_bell_but_kept_in_audit(client):
    stem, ids = _mk_screened_role()
    _decide(client, stem, ids[0], "forward")
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    client.put("/api/settings/notifications", headers=_as("SuperAdmin"),
               json={"expiry_days": 1})
    # backdate every notify event past the 1-day window
    ev = Path("signals/events.jsonl")
    rewritten = []
    for line in ev.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("kind") == "notify":
            e["ts"] = "2020-01-01T00:00:00+00:00"
        rewritten.append(json.dumps(e))
    ev.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    bell = client.get("/api/notifications", headers=_as("HiringManager")).json()
    assert bell["notifications"] == []  # hidden from the bell
    from resume_screener.events import load_events
    assert any(e["kind"] == "notify" for e in load_events())  # audit keeps them


# --- pack proposals carry a real from→to diff ------------------------------------------


def test_policy_pack_proposal_diff_has_from_and_to(client):
    r = client.post("/api/changes", headers=_as("HRHead"), json={
        "kind": "policy_pack", "target": "pack:IN",
        "payload": {"keep_at_least_days": 730},
        "rationale": "align with the retention counsel note",
    })
    assert r.status_code == 200, r.text
    diff = r.json()["change"]["diff"]
    row = next(d for d in diff if d["path"] == "keep_at_least_days")
    assert row["from"] not in ("", None) and row["to"].startswith("730")
