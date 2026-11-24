"""P34: POST /api/roles/{req_id}/decisions/batch — one review action applied to
many candidates. All-or-nothing validation, one appended Decision line per
candidate with the SSO reviewer, and the clear action returning candidates to
undecided. Offline: tmp_path workspace + synthetic run output."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.models import candidate_id_for
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]
FEEDBACK = Path("output/feedback/decisions.jsonl")


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


def _record(cid: str) -> dict:
    return {
        "score": {
            "candidate_id": cid, "role_title": "Treasury Analyst", "jurisdiction": "IN",
            "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                       "must_haves_missing": [], "coverage_score": 0.8,
                       "base_score": 80.0, "eligible": True, "reasons": []},
            "layer2": None, "critic": None, "final_score": 80.0,
            "fit_band": "Strong", "confidence": "high", "summary_rationale": "ok",
        },
        "findings": {"candidate_id": cid, "role_title": "Treasury Analyst",
                     "rubric_version": 1, "findings": [
                         {"requirement_id": "r01", "requirement_text": "US GAAP",
                          "match": "full", "evidence": "US GAAP", "note": None}]},
        "suspended_adjustments": [],
    }


@pytest.fixture()
def screened_role(client) -> dict:
    """One screened role with three attached candidates."""
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9010",
    })
    req_id = created.json()["req_id"]
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [{"text": "US GAAP", "kind": "must_have", "importance": 4}]})

    folder = Path(client.get(f"/api/roles/{req_id}/batch",
                             headers=_as("TASpecialist")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    cids = []
    for name in ("Anita Rao.pdf", "Vikram Shah.pdf", "Meera Roach.pdf"):
        data = name.encode("utf-8")
        (folder / name).write_bytes(data)
        cids.append(candidate_id_for(data))

    out = Path("output") / req_id
    (out / "candidates").mkdir(parents=True)
    for cid in cids:
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps(_record(cid)), encoding="utf-8")
    # Full current-config stamps (P51): decisions are 409-blocked on a drifted
    # run, so the synthetic run must look like it was produced under the rules
    # as they stand in this workspace.
    from resume_screener.cache import content_hash

    (out / "run_meta.json").write_text(json.dumps({
        "run_id": "run-1", "started_utc": "2026-07-12T08:00:00+00:00",
        "jurisdiction": "IN", "policy_pack": "IN", "counts": {"scored": len(cids)},
        "versions": {
            "rubric": f"{req_id}.rubric.v1.yaml (v1)",
            "policy_files": {
                "base": content_hash(Path("policies/base.yaml").read_bytes())[:12],
                "IN": content_hash(Path("policies/IN.yaml").read_bytes())[:12],
            },
        },
    }), encoding="utf-8")
    return {"req_id": req_id, "cids": cids}


def _log_lines() -> list[dict]:
    if not FEEDBACK.exists():
        return []
    return [json.loads(line) for line in FEEDBACK.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_batch_appends_one_line_per_candidate_with_sso_reviewer(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    response = client.post(f"/api/roles/{req_id}/decisions/batch",
                           headers=_as("TASpecialist"),
                           json={"candidate_ids": cids, "action": "discuss",
                                 "reason": "Need the HM's read on domain depth"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True and payload["applied"] == 3
    assert payload["decisions"] == {
        cid: {"action": "discuss", "reason": "Need the HM's read on domain depth"}
        for cid in cids}

    lines = _log_lines()
    assert len(lines) == 3
    for line in lines:
        assert line["human_action"] == "rerank"  # discuss
        assert line["final_band"] == "Possible"
        assert line["reason"] == "Need the HM's read on domain depth"  # PURE text
        assert line["reviewer"] == "taspecialist@example.com"  # the SSO email
        assert line["versions"]["rubric"].endswith("(v1)")
        assert line["suggestion"] == "Strong (80)"

    # and the review payload reflects all three
    cards = client.get(f"/api/roles/{req_id}/review",
                       headers=_as("TASpecialist")).json()["cards"]
    assert all(c["decided"] == "discuss" for c in cards)
    assert all(c["decided_reason"] == "Need the HM's read on domain depth" for c in cards)


def test_batch_clear_returns_candidates_to_undecided(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    client.post(f"/api/roles/{req_id}/decisions/batch", headers=_as("TASpecialist"),
                json={"candidate_ids": cids, "action": "forward"})
    response = client.post(f"/api/roles/{req_id}/decisions/batch",
                           headers=_as("TASpecialist"),
                           json={"candidate_ids": cids, "action": "clear"})
    assert response.status_code == 200

    assert len(_log_lines()) == 6  # append-only: the clear is three MORE lines
    cards = client.get(f"/api/roles/{req_id}/review",
                       headers=_as("TASpecialist")).json()["cards"]
    assert all(c["decided"] is None for c in cards)


def test_batch_is_all_or_nothing_on_an_unknown_id(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    response = client.post(f"/api/roles/{req_id}/decisions/batch",
                           headers=_as("TASpecialist"),
                           json={"candidate_ids": [cids[0], "not-a-cid"],
                                 "action": "forward"})
    assert response.status_code == 404
    assert "not-a-cid" in response.json()["detail"]
    assert _log_lines() == []  # NOTHING was appended — not even the valid id


def test_batch_rejects_empty_selection_and_bad_actions(client, screened_role):
    req_id = screened_role["req_id"]
    empty = client.post(f"/api/roles/{req_id}/decisions/batch",
                        headers=_as("TASpecialist"),
                        json={"candidate_ids": [], "action": "forward"})
    assert empty.status_code == 400
    bad = client.post(f"/api/roles/{req_id}/decisions/batch",
                      headers=_as("TASpecialist"),
                      json={"candidate_ids": screened_role["cids"], "action": "yeet"})
    assert bad.status_code == 400
    assert _log_lines() == []
