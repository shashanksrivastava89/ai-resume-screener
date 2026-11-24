"""P34: the cleaned reason storage. Decision.reason is the recruiter's PURE
free text (the action lives in human_action + final_band); notes surface as
decided_reason / decided_ts on cards and seed the drawer; the resume route
serves inline previews on ?inline=1. Offline: tmp_path workspace fixtures."""

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
    """Two candidates; only the first has a private-map entry (a real PDF)."""
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9011",
    })
    req_id = created.json()["req_id"]
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [{"text": "US GAAP", "kind": "must_have", "importance": 4}]})

    folder = Path(client.get(f"/api/roles/{req_id}/batch",
                             headers=_as("TASpecialist")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    cids, paths = [], []
    for name in ("Anita Rao.pdf", "Vikram Shah.pdf"):
        data = name.encode("utf-8")
        (folder / name).write_bytes(data)
        cids.append(candidate_id_for(data))
        paths.append(folder / name)

    out = Path("output") / req_id
    (out / "candidates").mkdir(parents=True)
    for cid in cids:
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps(_record(cid)), encoding="utf-8")
    # Full current-config stamps (P51): decisions are 409-blocked on a drifted
    # run, so the synthetic run must match this workspace's published rules.
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
    (out / "private").mkdir()
    (out / "private" / "candidate_map.json").write_text(
        json.dumps({cids[0]: str(paths[0])}), encoding="utf-8")
    return {"req_id": req_id, "mapped": cids[0], "unmapped": cids[1]}


def _decide(client, req_id, cid, action, reason=None):
    body = {"candidate_id": cid, "action": action}
    if reason is not None:
        body["reason"] = reason
    response = client.post(f"/api/roles/{req_id}/decisions",
                           headers=_as("TASpecialist"), json=body)
    assert response.status_code == 200, response.text
    return response


def test_reason_round_trips_as_pure_free_text(client, screened_role):
    req_id, cid = screened_role["req_id"], screened_role["mapped"]
    _decide(client, req_id, cid, "forward", "Strong GST filing evidence")

    [line] = [json.loads(ln) for ln in FEEDBACK.read_text(encoding="utf-8").splitlines()]
    assert line["reason"] == "Strong GST filing evidence"  # NO "Move forward — " prefix
    assert line["human_action"] == "agree" and line["final_band"] == "Strong"

    card = next(c for c in client.get(f"/api/roles/{req_id}/review",
                                      headers=_as("TASpecialist")).json()["cards"]
                if c["candidate_id"] == cid)
    assert card["decided"] == "forward"
    assert card["decided_reason"] == "Strong GST filing evidence"
    assert card["decided_ts"] == line["ts"]


def test_noteless_decision_stores_the_label_but_displays_no_note(client, screened_role):
    req_id, cid = screened_role["req_id"], screened_role["mapped"]
    _decide(client, req_id, cid, "discuss")  # no reason typed

    [line] = [json.loads(ln) for ln in FEEDBACK.read_text(encoding="utf-8").splitlines()]
    assert line["reason"] == "Discuss"  # the non-empty-reason guarantee holds

    card = next(c for c in client.get(f"/api/roles/{req_id}/review",
                                      headers=_as("TASpecialist")).json()["cards"]
                if c["candidate_id"] == cid)
    assert card["decided"] == "discuss"
    assert card["decided_reason"] == ""  # ...but it is NOT a note


def test_detail_seeds_the_note_and_carries_resume_media_type(client, screened_role):
    req_id = screened_role["req_id"]
    _decide(client, req_id, screened_role["mapped"], "forward", "Solid treasury depth")

    detail = client.get(f"/api/roles/{req_id}/review/{screened_role['mapped']}",
                        headers=_as("TASpecialist")).json()
    assert detail["decision"]["action"] == "forward"
    assert detail["decision"]["reason"] == "Solid treasury depth"
    assert detail["decision"]["ts"]
    # the note is attributed so other TAs know who recorded it
    assert detail["decision"]["reviewer"]
    assert detail["resume_media_type"] == "pdf"

    bare = client.get(f"/api/roles/{req_id}/review/{screened_role['unmapped']}",
                      headers=_as("TASpecialist")).json()
    assert bare["decision"] is None
    assert bare["resume_media_type"] is None  # no private-map entry -> no preview


def test_resume_route_inline_vs_attachment_disposition(client, screened_role):
    req_id, cid = screened_role["req_id"], screened_role["mapped"]
    base = f"/api/roles/{req_id}/candidates/{cid}/resume"

    download = client.get(base, headers=_as("TASpecialist"))
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment")

    preview = client.get(base + "?inline=1", headers=_as("TASpecialist"))
    assert preview.status_code == 200
    assert preview.headers["content-disposition"].startswith("inline")
