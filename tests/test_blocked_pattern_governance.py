"""Phase 2 (2026-07-21): jurisdiction blocked patterns are edited through the
SAME second-leader change-proposal flow as every other scoring-affecting change,
and an LLM may PROPOSE them (never enforce). Pins:

  * publish_pack carries blocked_patterns (file backend) and the floor guard
    rejects redefining a base FLOOR pattern;
  * the change-proposal path (propose -> dry-run diff -> approve -> publish)
    round-trips a region pattern and blocks a floor redefinition at propose time;
  * policy_derive degrades to a heuristic (no Azure) and namespaces/validates
    derived drafts;
  * GET /api/jurisdictions exposes effective + own blocked patterns, and the
    derive route is rules_edit-gated and non-fatal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import change_requests as cr
from resume_screener import governance, policy
from resume_screener.policy import PolicyError, resolve_policy
from resume_screener.scoring_model import load_scoring_model
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
    (tmp_path / "signals").mkdir()
    for name in ("features.yaml", "scoring_model.v1.yaml"):
        shutil.copyfile(REPO_ROOT / "signals" / name, tmp_path / "signals" / name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _as(role: str) -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL": principal(f"{role.lower()}@example.com", [role])}


def _pattern(pid="in_notice_period", severity="warn", phrases=("notice period",)):
    return {"id": pid, "name": "Notice period", "reason": "A proxy worth a look.",
            "severity": severity, "floor": False, "source": "manual",
            "stages": ["preferences", "checklist"], "phrases": list(phrases)}


# --- governance.publish_pack carries blocked_patterns (file backend) ----------------


def test_publish_pack_adds_blocked_pattern_and_resolves(workspace):
    v = governance.publish_pack("IN", blocked_patterns=[_pattern()],
                                author="hr@x", rationale="pilot proxy")
    assert v == 3  # IN.yaml ships at v2
    resolved = {p.id for p in resolve_policy("IN").blocked_patterns}
    assert "in_notice_period" in resolved
    assert {"protected_demographic", "age"} <= resolved  # universal floor still there


def test_publish_pack_rejects_base_floor_redefinition(workspace):
    with pytest.raises(PolicyError, match="(?i)floor"):
        governance.publish_pack("IN", blocked_patterns=[_pattern(pid="age", severity="warn")],
                                author="hr@x", rationale="try to weaken age")


def test_publish_pack_db_backend_refuses_blocked_patterns(workspace, monkeypatch):
    monkeypatch.setattr(governance, "_use_policy_db", lambda: True)
    with pytest.raises(PolicyError, match="file backend"):
        governance.publish_pack("IN", blocked_patterns=[_pattern()],
                                author="hr@x", rationale="db path")


# --- the change-proposal flow ---------------------------------------------------------


def test_propose_approve_blocked_pattern_round_trip(workspace):
    rec = cr.propose_change("policy_pack", "pack:IN",
                            {"blocked_patterns": [_pattern()]},
                            rationale="India proxy", author="hr@x")
    assert rec["status"] == "pending"
    assert any("blocked pattern added" in r["label"] for r in rec["diff"])
    done = cr.approve_change(rec["change_id"], approver="legal@x")
    assert done["status"] == "approved"
    assert "in_notice_period" in {p.id for p in resolve_policy("IN").blocked_patterns}
    publish = [e for e in governance.load_audit() if e["action"] == "publish_pack"]
    assert publish and publish[-1]["approved_by"] == "legal@x"


def test_propose_rejects_floor_redefinition_at_dry_run(workspace):
    with pytest.raises(cr.ChangeRequestError, match="(?i)floor"):
        cr.propose_change("policy_pack", "pack:IN",
                          {"blocked_patterns": [_pattern(pid="disability", severity="warn")]},
                          rationale="weaken", author="hr@x")


# --- policy_derive: heuristic degrade + mapping ---------------------------------------


def test_derive_heuristic_when_no_rules_text(workspace):
    from resume_screener import policy_derive

    out = policy_derive.derive_blocked_patterns("US-CA", [], None, load_scoring_model())
    assert out["source"] == "heuristic" and out["patterns"] == [] and out["note"]


def test_derive_mapping_namespaces_and_validates():
    from resume_screener.policy_derive import _DerivedPattern, _to_blocked_pattern

    ok = _to_blocked_pattern(_DerivedPattern(
        id="salary_history", name="Salary history", reason="CA bars it",
        severity="blocked", phrases=["salary history"]), "US-CA")
    assert ok is not None and ok.id == "us_ca_salary_history" and ok.floor is False \
        and ok.source == "derived"
    # a draft with neither phrases nor regexes has nothing to match on -> dropped
    assert _to_blocked_pattern(_DerivedPattern(
        id="x", name="X", reason="r"), "US-CA") is None
    # a bad regex is dropped, not raised
    assert _to_blocked_pattern(_DerivedPattern(
        id="y", name="Y", reason="r", regexes=["("]), "US-CA") is None


# --- API surface ----------------------------------------------------------------------


def test_get_jurisdictions_exposes_blocked_patterns(client):
    body = client.get("/api/jurisdictions", headers=_as("HRHead")).json()
    ca = next(j for j in body["jurisdictions"] if j["code"] == "US-CA")
    eff_ids = {p["id"] for p in ca["blocked_patterns"]}
    own_ids = {p["id"] for p in ca["own_blocked_patterns"]}
    assert "ca_salary_history" in own_ids            # region-specific (editable)
    assert "ca_salary_history" in eff_ids
    assert {"protected_demographic", "age"} <= eff_ids  # universal floor inherited
    assert "protected_demographic" not in own_ids       # ...but not in the overlay's own


def test_derive_route_is_rules_edit_gated_and_nonfatal(client, monkeypatch):
    # TASpecialist lacks rules_edit -> 403
    assert client.post("/api/jurisdictions/US-CA/derive", headers=_as("TASpecialist")
                       ).status_code == 403
    # stub the derivation so the route test never reaches Azure
    from resume_screener import policy_derive

    monkeypatch.setattr(policy_derive, "derive_blocked_patterns",
                        lambda *a, **k: {"patterns": [_pattern()], "conflicts": [],
                                         "source": "model", "note": ""})
    r = client.post("/api/jurisdictions/US-CA/derive", headers=_as("HRHead"))
    assert r.status_code == 200
    out = r.json()
    assert out["source"] == "model" and out["patterns"][0]["id"] == "in_notice_period"
    # base is not derivable
    assert client.post("/api/jurisdictions/base/derive", headers=_as("HRHead")
                       ).status_code == 400
