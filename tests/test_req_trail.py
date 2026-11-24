"""P29: GET /api/roles/{req_id}/trail — the workspace Audit tab's composition,
from synthetic tmp_path fixtures only (sidecar + rubric files + events + run_meta
+ decisions + private map). Recruiter language, no hashes.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import emit_event
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

STEM = "finance-analyst"
CID = "a1b2c3d4e5f60718"  # a 16-hex candidate id that must never surface


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds")


def _mk_fixtures(run_id="run-1"):
    """One role with a created stamp, two checklist versions, one run, one
    decision, and a private map that resolves the candidate to a human name."""
    jd = Path("jds") / f"{STEM}.md"
    jd.write_text("# Finance Analyst\n\nbody", encoding="utf-8")
    save_req_meta(jd, job_location="IN", created_by="taspecialist@example.com",
                  created_at=_iso(days_ago=40))
    for version in (1, 2):
        (Path("jds") / f"{STEM}.rubric.v{version}.yaml").write_text(
            "role_title: Finance Analyst\n", encoding="utf-8")

    out = Path("output") / STEM
    (out / "candidates").mkdir(parents=True)
    (out / "candidates" / f"{CID}.json").write_text(
        json.dumps({"score": {"candidate_id": CID, "fit_band": "Strong",
                              "final_score": 82.0}}), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": run_id, "started_utc": _iso(days_ago=10), "policy_pack": "IN",
        "counts": {"scored": 1},
        "versions": {"rubric": f"{STEM}.rubric.v2.yaml (v2)"},
    }), encoding="utf-8")
    (out / "private").mkdir()
    (out / "private" / "candidate_map.json").write_text(
        json.dumps({CID: f"data/roles/{STEM}/resumes/Bhavika Sharma Resume.pdf"}),
        encoding="utf-8")

    feedback = Path("output/feedback/decisions.jsonl")
    feedback.parent.mkdir(parents=True)
    feedback.write_text(json.dumps({
        "candidate_id": CID, "run_id": run_id, "suggestion": "Strong (82)",
        "human_action": "agree", "final_band": "Strong",
        "reason": "Move forward — strong treasury depth",
        "reviewer": "ta@example.com", "versions": {}, "ts": _iso(days_ago=5),
    }) + "\n", encoding="utf-8")


def test_trail_composition_and_ordering(client):
    _mk_fixtures()
    emit_event("req_close", actor="head@example.com", req_id=STEM,
               summary="Closed — hired 1")
    emit_event("req_close", actor="x", req_id="another-role",
               summary="someone else's event")

    trail = client.get(f"/api/roles/{STEM}/trail", headers=_as("TASpecialist")).json()
    kinds = [e["kind"] for e in trail["entries"]]
    assert kinds.count("created") == 1
    assert kinds.count("checklist") == 2
    assert kinds.count("req_close") == 1  # the other role's event is filtered out
    assert kinds.count("run") == 1

    created = next(e for e in trail["entries"] if e["kind"] == "created")
    assert created["actor"] == "taspecialist@example.com"
    checklists = {e["summary"] for e in trail["entries"] if e["kind"] == "checklist"}
    assert checklists == {"Checklist v1 approved", "Checklist v2 approved"}
    run = next(e for e in trail["entries"] if e["kind"] == "run")
    assert "IN hiring rules" in run["summary"]
    assert "checklist v2" in run["summary"]
    assert "run-1" in run["summary"]

    # newest first, and the honesty footnote for the pre-spine run
    stamps = [e["ts"] for e in trail["entries"]]
    assert stamps == sorted(stamps, reverse=True)
    assert "iCIMS" in trail["note"]
    assert "before this trail existed" in trail["note"]


def test_trail_decision_summary_quotes_verbatim(client):
    _mk_fixtures()
    trail = client.get(f"/api/roles/{STEM}/trail", headers=_as("TASpecialist")).json()
    summary = trail["decision_summary"]
    assert summary["forward"] == 1 and summary["discuss"] == 0 and summary["not"] == 0
    [quote] = summary["quotes"]
    assert quote["name"] == "Bhavika Sharma"  # via the private map, never the hash
    assert quote["action"] == "Move forward"
    assert quote["reason"] == "Move forward — strong treasury depth"
    assert quote["reviewer"] == "ta@example.com"
    assert quote["ts"]
    # no candidate hash anywhere in the payload
    assert CID not in json.dumps(trail)


def test_post_spine_runs_come_from_events_not_run_meta(client):
    _mk_fixtures(run_id="run-2")
    emit_event("screen_completed", actor="ta@example.com", req_id=STEM,
               summary="Screened 1 candidate — IN hiring rules · checklist v2 · run run-2",
               data={"run_id": "run-2", "pack": "IN"})

    trail = client.get(f"/api/roles/{STEM}/trail", headers=_as("TASpecialist")).json()
    kinds = [e["kind"] for e in trail["entries"]]
    assert kinds.count("screen_completed") == 1
    assert kinds.count("run") == 0  # no duplicate synthesized from run_meta
    assert "before this trail existed" not in trail["note"]


def test_trail_for_bare_role_is_empty_but_valid(client):
    jd = Path("jds") / f"{STEM}.md"
    jd.write_text("# Finance Analyst\n\nbody", encoding="utf-8")
    trail = client.get(f"/api/roles/{STEM}/trail", headers=_as("TASpecialist")).json()
    assert trail["entries"] == []
    assert trail["decision_summary"] == {"forward": 0, "discuss": 0, "not": 0,
                                         "quotes": []}
    assert "iCIMS" in trail["note"]
