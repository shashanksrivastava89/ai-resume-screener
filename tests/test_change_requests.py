"""Change proposals: the second-leader gate on every scoring-affecting publish.

Since the 2026-07-18 merge a scoring_model proposal carries the FULL draft
document (knobs + signals + topics). Pins: propose dry-runs (bad payloads fail
fast), leveled system warnings travel on the record, supersede semantics,
self-approval 403, withdraw-by-proposer, version-drift 409, approve executes
the real publish with every rail (audit entry carries approved_by + change_id;
approved publishes do NOT reopen the review banner), decline needs a note, and
the floor-topic acknowledgment over the API."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import change_requests as cr
from resume_screener import governance
from resume_screener.scoring_model import canonical_dump, load_scoring_model
from tests.test_sso_identity import principal

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


def _as(role: str, email: str | None = None) -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL": principal(email or f"{role.lower()}@example.com", [role])}


def _draft(**edits) -> dict:
    """The current model as a draft document, with dunder-path edits applied
    (bands__strong_at=85 → data['bands']['strong_at'] = 85)."""
    data = canonical_dump(load_scoring_model())
    for path, value in edits.items():
        node = data
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return data


def _payload(**edits) -> dict:
    return {"model": _draft(**edits)}


def _bands_body() -> dict:
    return {"kind": "scoring_model", "target": "scoring_model",
            "payload": _payload(bands__strong_at=85),
            "rationale": "July calibration: 88 left the top band empty."}


# --- propose: dry-run, warnings, supersede ---------------------------------------------


def test_propose_scoring_change_and_summary(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    assert rec["status"] == "pending"
    assert rec["base_version"] == 1
    assert rec["summary"] == ["“Strong match” starts at: 88 → 85"]
    assert rec["system_warnings"] == []
    # an audit entry records the proposal itself
    actions = [e["action"] for e in governance.load_audit()]
    assert "propose_change" in actions


def test_propose_rejects_invalid_payload_fast(workspace):
    with pytest.raises(cr.ChangeRequestError, match="Possible < Strong"):
        cr.propose_change("scoring_model", "scoring_model",
                          _payload(bands__strong_at=30),  # below possible_at=60
                          rationale="oops", author="hr@x")
    with pytest.raises(cr.ChangeRequestError, match="reason for the change"):
        cr.propose_change("scoring_model", "scoring_model",
                          _payload(bands__strong_at=85),
                          rationale="  ", author="hr@x")
    with pytest.raises(cr.ChangeRequestError, match="Nothing changed"):
        cr.propose_change("scoring_model", "scoring_model", _payload(),
                          rationale="noop", author="hr@x")


def test_out_of_usual_value_warns_but_is_allowed(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=97),
                            rationale="tighten hard", author="hr@x")
    assert rec["status"] == "pending"
    warnings = rec["system_warnings"]
    assert any("outside the usual range" in w["text"] and w["level"] == "warn"
               for w in warnings)


def test_new_pending_supersedes_old_for_same_target(workspace):
    first = cr.propose_change("scoring_model", "scoring_model",
                              _payload(bands__strong_at=85),
                              rationale="first", author="hr@x")
    second = cr.propose_change("scoring_model", "scoring_model",
                               _payload(bands__strong_at=84),
                               rationale="second", author="legal@x")
    folded = {c["change_id"]: c for c in cr.list_changes()}
    assert folded[first["change_id"]]["status"] == "superseded"
    assert folded[first["change_id"]]["superseded_by"] == second["change_id"]
    assert folded[second["change_id"]]["status"] == "pending"
    assert cr.pending_count() == 1


# --- approve: second-leader gate + the real publish ----------------------------------


def test_approve_executes_publish_with_rails(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    done = cr.approve_change(rec["change_id"], approver="legal@x")
    assert done["status"] == "approved" and done["resulting_version"] == 2

    sm = load_scoring_model()
    assert (sm.version, sm.bands.strong_at) == (2, 85)
    assert Path("signals/scoring_model.v1.yaml").exists()  # old version untouched

    publish = [e for e in governance.load_audit() if e["action"] == "publish_scoring_model"]
    assert publish and publish[-1]["approved_by"] == "legal@x"
    assert publish[-1]["change_id"] == rec["change_id"]
    assert publish[-1]["author"] == "hr@x"  # the proposer stays the author


def test_self_approval_is_refused(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="HR@X")
    with pytest.raises(cr.SelfApprovalError):
        cr.approve_change(rec["change_id"], approver="hr@x")  # case-insensitive


def test_withdraw_is_proposer_only(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    with pytest.raises(cr.SelfApprovalError, match="Only the proposer"):
        cr.withdraw_change(rec["change_id"], author="legal@x")
    done = cr.withdraw_change(rec["change_id"], author="hr@x")
    assert done["status"] == "withdrawn"
    assert cr.pending_count() == 0
    assert load_scoring_model().version == 1  # nothing changed
    assert any(e["action"] == "withdraw_change" for e in governance.load_audit())


def test_version_drift_409s_the_approval(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    # An out-of-band publish moves the version under the proposal.
    governance.publish_scoring_model(_draft(bands__possible_at=55),
                                     author="cli@x", rationale="direct")
    with pytest.raises(cr.ChangeConflictError, match="changed since this was proposed"):
        cr.approve_change(rec["change_id"], approver="legal@x")


def test_approved_publish_does_not_reopen_review_banner(workspace):
    governance.record_review("HR + Legal", "2026-07-01", author="hr@x")
    assert governance.review_pending() is False
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    cr.approve_change(rec["change_id"], approver="legal@x")
    assert governance.review_pending() is False  # dual review already happened
    # ...but a DIRECT (unapproved) publish still reopens it.
    governance.publish_scoring_model(_draft(bands__possible_at=55),
                                     author="cli@x", rationale="direct")
    assert governance.review_pending() is True


def test_decline_needs_a_note_and_records_it(workspace):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    with pytest.raises(cr.ChangeRequestError, match="note"):
        cr.decline_change(rec["change_id"], note=" ", approver="legal@x")
    done = cr.decline_change(rec["change_id"], note="Wait for the July batch.",
                             approver="legal@x")
    assert done["status"] == "declined" and done["decline_note"] == "Wait for the July batch."
    assert any(e["action"] == "decline_change" for e in governance.load_audit())


# --- signal + topic edits ride the same draft ------------------------------------------


def test_signal_cap_edit_via_proposal(workspace):
    data = _draft()
    next(s for s in data["signals"] if s["id"] == "skill_recency")["cap_points"] = 4.0
    rec = cr.propose_change("scoring_model", "scoring_model", {"model": data},
                            rationale="recency deserves more room", author="hr@x")
    done = cr.approve_change(rec["change_id"], approver="legal@x")
    from resume_screener.registry import load_registry

    reg = load_registry("signals")
    assert done["resulting_version"] == reg.version
    assert next(s for s in reg.signals if s.id == "skill_recency").cap_points == 4.0


def test_floor_topic_removal_needs_acknowledgment_and_reds(workspace):
    data = _draft()
    data["topics"] = [t for t in data["topics"] if t["id"] != "salary_history"]
    # without the acknowledgment the dry run refuses
    with pytest.raises(cr.ChangeRequestError, match="policy-floor protection"):
        cr.propose_change("scoring_model", "scoring_model", {"model": data},
                          rationale="counsel confirmed", author="admin@x")
    rec = cr.propose_change(
        "scoring_model", "scoring_model",
        {"model": data, "acknowledged_floor_removals": ["salary_history"]},
        rationale="counsel confirmed the ground changed", author="admin@x")
    assert any(w["level"] == "red" and "policy-floor" in w["text"]
               for w in rec["system_warnings"])
    cr.approve_change(rec["change_id"], approver="hr@x")
    from resume_screener.registry import load_registry

    assert "salary_history" not in {e.id for e in load_registry("signals").excluded}


def test_direct_publish_still_needs_the_acknowledgment(workspace):
    from resume_screener.scoring_model import ScoringModelError

    data = _draft()
    data["topics"] = [t for t in data["topics"] if t["id"] != "salary_history"]
    with pytest.raises(ScoringModelError, match="policy-floor protection"):
        governance.publish_scoring_model(data, author="hr@x", rationale="nope")


# --- the API surface -----------------------------------------------------------------


def test_api_full_flow_propose_approve(client):
    r = client.post("/api/changes", headers=_as("HRHead"), json=_bands_body())
    assert r.status_code == 200
    change_id = r.json()["change"]["change_id"]

    # queue visible to an approver; badge count on /api/me
    q = client.get("/api/changes?status=pending", headers=_as("Legal")).json()
    assert q["pending_count"] == 1
    me = client.get("/api/me", headers=_as("Legal")).json()
    assert me["changes_pending"] == 1

    # self-approval 403, second leader 200
    assert client.post(f"/api/changes/{change_id}/approve",
                       headers=_as("HRHead")).status_code == 403
    ok = client.post(f"/api/changes/{change_id}/approve", headers=_as("Legal"))
    assert ok.status_code == 200 and ok.json()["version"] == 2


def test_api_withdraw_by_proposer(client):
    r = client.post("/api/changes", headers=_as("HRHead"), json=_bands_body())
    change_id = r.json()["change"]["change_id"]
    assert client.post(f"/api/changes/{change_id}/withdraw",
                       headers=_as("Legal")).status_code == 403
    ok = client.post(f"/api/changes/{change_id}/withdraw", headers=_as("HRHead"))
    assert ok.status_code == 200 and ok.json()["change"]["status"] == "withdrawn"


def test_api_decline_requires_note_and_ta_cannot_propose(client):
    r = client.post("/api/changes", headers=_as("HRHead"), json=_bands_body())
    change_id = r.json()["change"]["change_id"]
    assert client.post(f"/api/changes/{change_id}/decline", headers=_as("Legal"),
                       json={"note": ""}).status_code == 400
    assert client.post("/api/changes", headers=_as("TASpecialist"),
                       json=_bands_body()).status_code == 403


def test_api_old_instant_publish_routes_are_gone(client):
    # GET+PUT /api/signals died with the merge; the pack PUT died with P51.
    assert client.get("/api/signals", headers=_as("HRHead")).status_code == 404
    assert client.put("/api/signals", headers=_as("HRHead"),
                      json={"rationale": "x"}).status_code in (404, 405)
    assert client.put("/api/jurisdictions/IN", headers=_as("HRHead"),
                      json={"rationale": "x"}).status_code == 405
