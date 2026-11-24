"""The hiring decisions are the requisition team's (2026-10-21, owner's decision).

Move forward / Discuss / Not a fit — with the note, who made it and when, and the
hiring manager's take — are shown only to the people working the requisition: the
recruiter who owns it, anyone it is shared with to collaborate, its hiring manager,
and HR Heads. Everyone else who can open it (Legal, an administrator) sees every
candidate and the AI's read on them, and no decision anywhere in the workspace: not
on a card, in the drawer, in the decisions popup, in an export or in the Ask-Claude
bundle — and they cannot record one blind. Audit & History keeps the record.
"""

from __future__ import annotations

import json

import pytest

from resume_screener.ats.local_folder import save_req_meta
from tests.test_sso_identity import _as, principal
from tests.test_verdict_view import (  # noqa: F401 — the shared screened requisition
    STEM, _decide, _screened, _share, client,
)

REASON = "Spoke to them"   # the note _decide records
SUPER_ADMIN_ONLY = {"X-MS-CLIENT-PRINCIPAL": principal("admin@example.com", ["SuperAdmin"])}
OUTSIDERS = {"Legal": _as("Legal"), "SuperAdmin": SUPER_ADMIN_ONLY}
DECISION_KEYS = {"decided", "decided_reason", "decided_ts", "decided_by",
                 "decided_reconfirm", "hm_take", "decision", "decision_counts"}


def _decision_leaks(obj, path="$") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            # a number is a progress count ("12 reviewed", the close nudge's tally),
            # which says how many were decided — never which way
            if k in DECISION_KEYS and not isinstance(v, (int, float)):
                found.append(f"{path}.{k}")
            found += _decision_leaks(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _decision_leaks(v, f"{path}[{i}]")
    elif isinstance(obj, str) and REASON in obj:
        found.append(f"{path} says {REASON!r}")
    return found


def _decided(client) -> dict[str, str]:
    ids = _screened()
    _decide(client, ids["Anita Rao"], "forward")
    _decide(client, ids["Chitra Das"], "not")
    return ids


def _with_collaborator(client) -> None:
    r = client.post(f"/api/roles/{STEM}/collaborators", headers=_as("TASpecialist"),
                    json={"email": "hrspecialist@example.com", "name": "Hema Rao"})
    assert r.status_code == 200, r.text


TEAM = {"owner": _as("TASpecialist"), "collaborator": _as("HRSpecialist"),
        "HR Head": _as("HRHead"), "hiring manager": _as("HiringManager")}


@pytest.mark.parametrize("who", list(TEAM))
def test_the_team_sees_the_decisions(client, who):
    ids = _decided(client)
    _with_collaborator(client)
    _share(client)
    h = TEAM[who]
    review = client.get(f"/api/roles/{STEM}/review", headers=h).json()
    assert review["decisions_visible"] is True
    anita = next(c for c in review["cards"] if c["name"] == "Anita Rao")
    assert anita["decided"] == "forward" and anita["decided_reason"] == REASON
    detail = client.get(f"/api/roles/{STEM}/review/{ids['Anita Rao']}", headers=h).json()
    assert detail["decisions_visible"] is True and detail["decision"]["action"] == "forward"


@pytest.mark.parametrize("who", list(OUTSIDERS))
def test_outside_the_team_every_candidate_shows_and_no_decision(client, who):
    ids = _decided(client)
    _share(client)
    h = OUTSIDERS[who]
    review = client.get(f"/api/roles/{STEM}/review", headers=h).json()
    # every candidate, with the AI's read …
    assert len(review["cards"]) == 3 and all(c.get("band") for c in review["cards"])
    # … and nothing the team decided
    assert review["decisions_visible"] is False and review["hm_updates"] is None
    detail = client.get(f"/api/roles/{STEM}/review/{ids['Anita Rao']}", headers=h).json()
    assert detail["decisions_visible"] is False and detail["reqs"]
    for url in ("/api/roles", f"/api/roles/{STEM}", f"/api/roles/{STEM}/review",
                f"/api/roles/{STEM}/review?blind=true",
                f"/api/roles/{STEM}/review/{ids['Anita Rao']}"):
        body = client.get(url, headers=h).json()
        assert _decision_leaks(body) == [], (who, url)

    popup = client.get(f"/api/roles/{STEM}/decisions", headers=h).json()
    assert popup["hidden"] is True and popup["decisions"] == []
    assert "shown to its team" in popup["message"]

    csv = client.get(f"/api/roles/{STEM}/shortlist/export.csv", headers=h)
    assert csv.status_code == 200
    header = csv.text.splitlines()[0].split(",")
    for col in ("Decision", "Decision note", "Noted by", "Decided on",
                "Hiring manager take", "Resume file"):
        assert col not in header, (who, col)
    assert REASON not in csv.text and "Move forward" not in csv.text

    zipped = client.post(f"/api/roles/{STEM}/shortlist/download", headers=h,
                         json={"candidate_ids": [ids["Anita Rao"]]})
    assert zipped.status_code == 403 and "shown to its team" in zipped.json()["detail"]

    bundle = client.post(f"/api/roles/{STEM}/handoff", headers=h, json={})
    assert bundle.status_code == 200, bundle.text
    assert "Decisions so far" not in bundle.json()["text"]


def test_outside_the_team_nobody_decides_blind(client):
    ids = _decided(client)
    h = SUPER_ADMIN_ONLY
    one = client.post(f"/api/roles/{STEM}/decisions", headers=h,
                      json={"candidate_id": ids["Upkaar Roach"], "action": "forward"})
    many = client.post(f"/api/roles/{STEM}/decisions/batch", headers=h,
                       json={"candidate_ids": [ids["Upkaar Roach"]], "action": "discuss"})
    share = client.post(f"/api/roles/{STEM}/share-hm", headers=h)
    for r in (one, many, share):
        assert r.status_code == 403 and "made by its team" in r.json()["detail"], r.text


def test_an_administrator_sees_the_decisions_on_a_requisition_of_their_own(client):
    from pathlib import Path

    ids = _decided(client)
    save_req_meta(Path("jds") / f"{STEM}.md", created_by="admin@example.com")
    review = client.get(f"/api/roles/{STEM}/review", headers=SUPER_ADMIN_ONLY).json()
    assert review["decisions_visible"] is True
    assert next(c for c in review["cards"]
                if c["candidate_id"] == ids["Anita Rao"])["decided"] == "forward"


def test_the_team_downloads_the_shortlist_with_its_decisions(client):
    ids = _decided(client)
    zipped = client.post(f"/api/roles/{STEM}/shortlist/download", headers=_as("HRHead"),
                         json={"candidate_ids": [ids["Anita Rao"]]})
    assert zipped.status_code == 200
    csv = client.get(f"/api/roles/{STEM}/shortlist/export.csv", headers=_as("HRHead"))
    assert "Decision" in csv.text.splitlines()[0].split(",") and REASON in csv.text
    bundle = client.post(f"/api/roles/{STEM}/handoff", headers=_as("TASpecialist"), json={})
    assert "Decisions so far" in bundle.json()["text"]


def test_audit_and_history_keeps_the_record(client):
    """The decision record stays on Audit & History for the roles that audit it —
    that is what `runs_audit` is for. Only the workspace follows the team rule."""
    _decided(client)
    feed = client.get("/api/audit/feed?kind=Decision", headers=_as("Legal")).json()
    assert any(REASON in e["summary"] for e in feed["entries"])


def test_the_mcp_connector_follows_the_same_rule(client, monkeypatch):
    from resume_screener import feature_flags
    from resume_screener.api import mcp

    feature_flags.set_flag("ask_claude_mcp", None, True, "test-setup")
    _decided(client)
    monkeypatch.setenv("DEV_USER", "admin@example.com")
    monkeypatch.setenv("DEV_ROLE", "SuperAdmin")
    shortlist = mcp.review_shortlist(STEM)
    assert shortlist["decisions_visible"] is False and shortlist["decision_counts"] is None
    assert all("decision" not in row for row in shortlist["candidates"])
    assert "decision" not in mcp.candidate_detail(STEM, 1)
    assert "Decisions so far" not in mcp.ask_claude_bundle(STEM)["text"]
    assert REASON not in json.dumps(shortlist)
