"""P33: the gate-aware Review payload. A gate-failed candidate keeps their rank
and listing (never hidden), reads "Gate not met", the counts split
gates_passed / gate_not_met, and the drawer detail carries gate_warn.

Offline: tmp_path workspace + synthetic run output (never real output/)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.models import candidate_id_for
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


GATE_TEXT = "CA qualification stated"
GATE_REASON = f"Hard requirement not met: {GATE_TEXT}"


def _record(cid: str, *, passes: bool) -> dict:
    """A synthetic per-candidate run record in the report writer's JSON shape."""
    layer1 = {
        "candidate_id": cid, "rubric_version": 1,
        "gate_flags": [] if passes else ["r02"],
        "must_haves_missing": [],
        "coverage_score": 0.82 if passes else 0.45,
        "base_score": 82.0 if passes else 45.0,
        "eligible": passes,
        "reasons": [] if passes else [GATE_REASON],
    }
    return {
        "score": {
            "candidate_id": cid, "role_title": "Treasury Analyst", "jurisdiction": "IN",
            "layer1": layer1, "layer2": None, "critic": None,
            "final_score": layer1["base_score"],
            "fit_band": "Strong" if passes else "Not a fit",
            "confidence": "high",
            "summary_rationale": "ok" if passes else f"Not a fit (gate): {GATE_REASON}",
        },
        "findings": {"candidate_id": cid, "role_title": "Treasury Analyst",
                     "rubric_version": 1, "findings": [
                         {"requirement_id": "r01", "requirement_text": "US GAAP",
                          "match": "full", "evidence": "US GAAP", "note": None},
                         {"requirement_id": "r02", "requirement_text": GATE_TEXT,
                          "match": "full" if passes else "partial",
                          "evidence": "CA", "note": None},
                     ]},
        "suspended_adjustments": [],
    }


@pytest.fixture()
def screened_role(client) -> dict:
    """One role, a gated checklist, two attached résumés (one passes the gate,
    one doesn't) and their synthetic run output."""
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9002",
    })
    req_id = created.json()["req_id"]
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [
            {"text": "US GAAP", "kind": "must_have", "importance": 4},
            {"text": GATE_TEXT, "kind": "hard_gate", "importance": 3},
        ]})

    # Attach two synthetic résumés; their content hashes ARE the candidate ids.
    folder = Path(client.get(f"/api/roles/{req_id}/batch",
                             headers=_as("TASpecialist")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    ids = {}
    for name, passes in (("Anita Rao.pdf", True), ("Vikram Shah.pdf", False)):
        data = name.encode("utf-8")
        (folder / name).write_bytes(data)
        ids[name] = (candidate_id_for(data), passes)

    out = Path("output") / req_id
    (out / "candidates").mkdir(parents=True)
    for cid, passes in ids.values():
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps(_record(cid, passes=passes)), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": "run-1", "started_utc": "2026-07-12T08:00:00+00:00",
        "jurisdiction": "IN", "policy_pack": "IN",
        "counts": {"scored": 2},
        "versions": {"rubric": f"{req_id}.rubric.v1.yaml (v1)"},
    }), encoding="utf-8")
    passer = next(cid for cid, ok in ids.values() if ok)
    failer = next(cid for cid, ok in ids.values() if not ok)
    return {"req_id": req_id, "passer": passer, "failer": failer}


def test_gate_failed_card_stays_listed_as_gate_not_met(client, screened_role):
    payload = client.get(f"/api/roles/{screened_role['req_id']}/review",
                         headers=_as("TASpecialist")).json()
    assert payload["ready"] is True
    cards = payload["cards"]
    assert len(cards) == 2  # the gate-failer is LISTED, never hidden

    by_id = {c["candidate_id"]: c for c in cards}
    failed = by_id[screened_role["failer"]]
    passed = by_id[screened_role["passer"]]

    assert failed["band_label"] == "Gate not met"
    assert failed["band"] == "Not a fit"  # internal band unchanged
    # The fixture's failer has PARTIAL evidence for the gate (match "partial"): layer 1
    # needs "full" to pass one, so the card says so separately — "close, and not counted"
    # is a human's call, "nothing there" is settled (2026-09-07).
    assert failed["gates"] == {"total": 1, "failed": [GATE_TEXT], "partial": [GATE_TEXT]}
    assert failed["rank"] == 2  # ranked below real candidates, still ranked

    assert passed["band_label"] == "Strong match"
    assert passed["gates"] == {"total": 1, "failed": [], "partial": []}


def test_counts_split_gates_passed_and_not_met(client, screened_role):
    counts = client.get(f"/api/roles/{screened_role['req_id']}/review",
                        headers=_as("TASpecialist")).json()["counts"]
    assert counts["gates_passed"] == 1
    assert counts["gate_not_met"] == 1
    assert counts["total"] == 2


def test_detail_carries_gate_warn_and_gate_tag(client, screened_role):
    req_id = screened_role["req_id"]
    detail = client.get(f"/api/roles/{req_id}/review/{screened_role['failer']}",
                        headers=_as("TASpecialist")).json()
    assert detail["band_label"] == "Gate not met"
    # 2026-09-07: gate_warn is the HEADLINE; the gates themselves are one item each
    # (a paragraph running three gate texts together was unreadable), and the rule +
    # the "you can still move them forward" caveat live in gate_rule, said once.
    assert detail["gate_warn"] == "1 hard requirement not met"
    assert detail["gate_items"] == [
        {"text": GATE_TEXT, "state": "partial", "evidence": "CA", "note": ""}]
    # a partial gate is raised as a "closer look" reason with somewhere to go
    assert any("part-way met" in f["text"] and f["where"] == "evidence"
               for f in detail["flag_items"])
    assert "pass or fail" in detail["gate_rule"] and "with a reason" in detail["gate_rule"]
    # The verdict no longer repeats the gate list the block above already shows.
    assert not detail["verdict"].startswith("Not a fit (gate)")
    assert GATE_TEXT not in detail["verdict"]
    gate_rows = [r for r in detail["reqs"] if r["gate"]]
    assert [r["label"] for r in gate_rows] == [GATE_TEXT]

    clean = client.get(f"/api/roles/{req_id}/review/{screened_role['passer']}",
                       headers=_as("TASpecialist")).json()
    assert clean["gate_warn"] == ""
    assert clean["band_label"] == "Strong match"
