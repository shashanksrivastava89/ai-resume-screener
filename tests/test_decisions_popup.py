"""The dashboard's recorded-decisions popup (design-led rebuild): the latest
decision per candidate on the current run, with display names + reviewers —
readable without opening the requisition. Plus the median-recent-move stat on
/api/scoring-model. Offline."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
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


def _mk_screened_role(stem="finance-analyst", with_adjustments=False):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    save_req_meta(jd, icims_req_id=f"REQ-{stem}", job_location="IN", status="active",
                  created_by="ta@example.com")
    out = Path("output") / stem
    (out / "candidates").mkdir(parents=True)
    (out / "private").mkdir(parents=True)
    resumes = Path("data/roles") / stem / "resumes"
    resumes.mkdir(parents=True)
    f = resumes / "Ananya Sharma.pdf"
    f.write_bytes(b"pdf-a")
    from resume_screener.api import service

    cid = service.cached_candidate_id(f)
    layer2 = ({"candidate_id": cid, "adjustments": [
                  {"signal_id": "skill_recency", "points": 3.0,
                   "evidence": "e", "rationale": "r"},
                  {"signal_id": "deal_profile_fit", "points": 2.0,
                   "evidence": "e", "rationale": "r"}],
               "proposed_signals": [], "narrative": "n"}
              if with_adjustments else None)
    (out / "candidates" / f"{cid}.json").write_text(json.dumps({
        "score": {"candidate_id": cid, "role_title": "Finance Analyst",
                  "jurisdiction": "IN",
                  "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                             "must_haves_missing": [], "coverage_score": 0.7,
                             "base_score": 70.0, "eligible": True, "reasons": []},
                  "layer2": layer2,
                  "critic": ({"verdict_per_adjustment": {"skill_recency": "pass",
                                                         "deal_profile_fit": "pass"},
                              "notes": [], "overall_flags": []}
                             if with_adjustments else None),
                  "preference_adjustments": [],
                  "final_score": 75.0 if with_adjustments else 70.0,
                  "fit_band": "Possible", "confidence": "high",
                  "summary_rationale": "ok"},
        "findings": {"candidate_id": cid, "role_title": "Finance Analyst",
                     "rubric_version": 1, "findings": []},
        "suspended_adjustments": [],
    }), encoding="utf-8")
    (out / "private" / "candidate_map.json").write_text(json.dumps({cid: str(f)}),
                                                        encoding="utf-8")
    from resume_screener.cache import content_hash
    from resume_screener.scoring_model import scoring_model_stamp

    (out / "run_meta.json").write_text(json.dumps(
        {"run_id": "run-1", "jurisdiction": "IN",
         # A few days ago, never a fixed date: the median reads the last 92 days,
         # so a hard-coded "2026-07-17" aged out on 18 Oct and failed every run since.
         "started_utc": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(
             timespec="seconds"),
         "counts": {"scored": 1},
         "versions": {
             "scoring_model": scoring_model_stamp(),
             "policy_files": {
                 "base": content_hash(Path("policies/base.yaml").read_bytes())[:12],
                 "IN": content_hash(Path("policies/IN.yaml").read_bytes())[:12],
             },
         }}), encoding="utf-8")
    return stem, cid


def test_decisions_popup_route(client):
    stem, cid = _mk_screened_role()
    client.post(f"/api/roles/{stem}/decisions", headers=_as("HRHead"),
                json={"candidate_id": cid, "action": "forward",
                      "reason": "Distributor network depth."})
    data = client.get(f"/api/roles/{stem}/decisions", headers=_as("HRHead")).json()
    assert data["title"] == "Finance Analyst"
    row = data["decisions"][0]
    assert row["name"] == "Ananya Sharma" and row["action"] == "forward"
    assert row["reason"] == "Distributor network depth."
    assert row["reviewer"] == "hrhead@example.com"
    # recruiter-gated (an HM never reads the decision log)
    assert client.get(f"/api/roles/{stem}/decisions",
                      headers=_as("HiringManager")).status_code == 403


def test_decisions_popup_empty_when_nothing_decided(client):
    stem, _cid = _mk_screened_role()
    data = client.get(f"/api/roles/{stem}/decisions", headers=_as("HRHead")).json()
    assert data["decisions"] == []


def test_median_recent_move_on_scoring_model(client):
    _mk_screened_role(with_adjustments=True)
    data = client.get("/api/scoring-model", headers=_as("HRHead")).json()
    assert data["median_recent_move"] == 5.0  # 3 + 2 net on the one candidate


def test_median_recent_move_null_without_adjustments(client):
    _mk_screened_role(with_adjustments=False)
    data = client.get("/api/scoring-model", headers=_as("HRHead")).json()
    assert data["median_recent_move"] is None
