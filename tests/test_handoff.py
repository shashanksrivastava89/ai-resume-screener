"""P44: the Ask-Claude handoff bundle.

The bundle is assembled server-side from the review records and is redacted BY
CONSTRUCTION — no contact details, file paths, source filenames, candidate ids
or emails. Blind mode numbers candidates. Every copy appends a handoff_copied
event. Offline: tmp_path workspace + TestClient/_as().
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import load_events
from resume_screener.models import candidate_id_for
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]
HM_EMAIL = "hiringmanager@example.com"


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
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml", tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener import feature_flags
    from resume_screener.api.app import app

    # The feature under test ships switched off (2026-10-23); these tests turn it on.
    feature_flags.set_flag("ask_claude_handoff", None, True, author="sa@example.com")
    return TestClient(app)


CANDIDATE_FILE = "Anita Rao.pdf"


def _screened_role(client, stem="finance-analyst", hm=None) -> tuple[str, str]:
    """A role with a checklist and one screened candidate whose id is the content
    hash of an attached résumé file. Returns (stem, candidate_id)."""
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    meta = {"icims_req_id": f"REQ-{stem}", "job_location": "IN", "status": "active",
            "created_by": "taspecialist@example.com"}
    if hm:
        meta["hm"] = {"email": hm, "name": "Rahul", "status": "invited"}
    save_req_meta(jd, **meta)

    # a published checklist
    client.put(f"/api/roles/{stem}/rubric", headers=_as("TASpecialist"), json={"requirements": [
        {"text": "Strong US GAAP experience", "kind": "must_have", "importance": 5},
        {"text": "CA qualification stated", "kind": "hard_gate", "importance": 3},
    ]})

    # attach a résumé; its content hash IS the candidate id (the review reconciler)
    folder = Path(client.get(f"/api/roles/{stem}/batch", headers=_as("TASpecialist")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    data = b"Anita Rao resume bytes"
    (folder / CANDIDATE_FILE).write_bytes(data)
    cid = candidate_id_for(data)

    out = Path("output") / stem
    # the private map (id -> source path) is the review UI's only id→name bridge;
    # candidate_name derives "Anita Rao" from the filename stem (never the path).
    (out / "private").mkdir(parents=True)
    (out / "private" / "candidate_map.json").write_text(
        json.dumps({cid: str(folder / CANDIDATE_FILE)}), encoding="utf-8")
    (out / "candidates").mkdir(parents=True)
    (out / "candidates" / f"{cid}.json").write_text(json.dumps({
        "score": {"candidate_id": cid, "role_title": "Finance Analyst", "jurisdiction": "IN",
                  "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                             "must_haves_missing": [], "coverage_score": 0.82, "base_score": 82.0,
                             "eligible": True, "reasons": []},
                  "layer2": None, "critic": None, "final_score": 82.0, "fit_band": "Strong",
                  "confidence": "high", "summary_rationale": "ok"},
        "findings": {"candidate_id": cid, "role_title": "Finance Analyst", "rubric_version": 1,
                     "findings": [
                         {"requirement_id": "r01", "requirement_text": "Strong US GAAP experience",
                          "match": "full", "evidence": "led US GAAP reporting for the India entity",
                          "note": ""},
                         {"requirement_id": "r02", "requirement_text": "CA qualification stated",
                          "match": "none", "evidence": None, "note": ""},
                     ]},
        "suspended_adjustments": [],
    }), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps(
        {"run_id": "run-1", "jurisdiction": "IN", "counts": {"scored": 1}}), encoding="utf-8")
    return stem, cid


def test_bundle_is_redacted_and_logged(client):
    stem, cid = _screened_role(client)
    r = client.post(f"/api/roles/{stem}/handoff", headers=_as("TASpecialist"), json={"blind": False})
    assert r.status_code == 200, r.text
    text = r.json()["text"]

    # useful content is present
    assert "# Screening context — Finance Analyst" in text
    assert "Strong US GAAP experience" in text          # checklist + evidence
    assert "led US GAAP reporting for the India entity" in text
    assert "Anita Rao" in text                           # display name (non-blind)

    # redacted BY CONSTRUCTION
    assert "@" not in text                               # no emails
    assert "output/" not in text and "data/" not in text  # no path fragments
    assert CANDIDATE_FILE not in text and ".pdf" not in text  # no source filenames
    assert cid not in text                               # no candidate id
    assert not re.search(r"\b[0-9a-f]{16}\b", text)      # no 16-hex id fragment

    # the copy is recorded
    events = load_events(req_id=stem, kinds=["handoff_copied"])
    assert events and "logged automatically" in events[-1]["summary"]


def test_blind_mode_numbers_candidates(client):
    stem, _cid = _screened_role(client)
    text = client.post(f"/api/roles/{stem}/handoff", headers=_as("TASpecialist"),
                       json={"blind": True}).json()["text"]
    assert "Candidate 1" in text
    assert "Anita Rao" not in text  # the name is withheld in blind mode


def test_non_assigned_hm_gets_403(client):
    # assigned to a DIFFERENT hiring manager → this HM can't reach the bundle
    stem, _cid = _screened_role(client, hm="someone.else@example.com")
    r = client.post(f"/api/roles/{stem}/handoff", headers=_as("HiringManager"), json={})
    assert r.status_code == 403


def test_ask_claude_is_off_until_a_flag_turns_it_on(workspace):
    """2026-10-23: the copy carries candidates' names and the AI's read to Claude,
    which waits on InfoSec. It ships switched off, and off means the SERVER refuses
    (not only a hidden button) and nothing is recorded as copied."""
    from fastapi.testclient import TestClient

    from resume_screener import feature_flags
    from resume_screener.api.app import app

    client = TestClient(app)
    stem, _cid = _screened_role(client)
    assert feature_flags.flag_enabled("ask_claude_handoff") is False   # as shipped

    r = client.post(f"/api/roles/{stem}/handoff", headers=_as("TASpecialist"), json={})
    assert r.status_code == 403 and "turned off" in r.json()["detail"]
    assert load_events(req_id=stem, kinds=["handoff_copied"]) == []

    feature_flags.set_flag("ask_claude_handoff", None, True, author="sa@example.com")
    r = client.post(f"/api/roles/{stem}/handoff", headers=_as("TASpecialist"), json={})
    assert r.status_code == 200 and r.json()["text"]
