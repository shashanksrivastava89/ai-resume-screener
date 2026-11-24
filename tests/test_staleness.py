"""P51 staleness: config drift detection, the mode state machine (user decision:
free changes auto-refresh, paid ones wait for the next screen; decisions lock),
and the payload surfaces (role card, review, preflight)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener import governance
from resume_screener.ats.local_folder import save_req_meta
from resume_screener.cache import content_hash
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


def _stamps():
    from resume_screener.api import service

    return service.current_config_stamps(service.config())


def _mk_role(stem="finance-analyst", title="Finance Analyst"):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text(f"# {title}\n\nOwns treasury accounting.", encoding="utf-8")
    save_req_meta(jd, job_location="IN", created_by="hr@example.com")
    rubric = REPO_ROOT / "jds"  # not used; rubric written below
    Path("jds", f"{stem}.rubric.v1.yaml").write_text(
        "role_title: Finance Analyst\njd_ref: jds/" + stem + ".md\nversion: 1\n"
        "requirements:\n"
        "  - {id: r01, text: GL accounting, type: skill, kind: must_have, weight: 1}\n",
        encoding="utf-8")
    return stem


def _mk_run(req_id, *, cids=("c1",), run_id="run-1", stamp_current=True, bands=("Possible",)):
    """A synthetic finished run: candidates/*.json + run_meta.json + private map,
    résumés attached so role_stage counts them."""
    from resume_screener.api import service

    resumes = Path("data/roles") / req_id / "resumes"
    resumes.mkdir(parents=True, exist_ok=True)
    out = Path("output") / req_id
    (out / "candidates").mkdir(parents=True)
    (out / "private").mkdir(parents=True)
    pmap = {}
    real_ids = []
    for i, cid in enumerate(cids):
        f = resumes / f"resume-{cid}.pdf"
        f.write_bytes(f"pdf-{cid}".encode())
        real = service.cached_candidate_id(f)
        real_ids.append(real)
        pmap[real] = str(f)
        band = bands[i % len(bands)]
        (out / "candidates" / f"{real}.json").write_text(json.dumps({
            "score": {"candidate_id": real, "role_title": "Finance Analyst",
                      "jurisdiction": "IN",
                      "layer1": {"candidate_id": real, "rubric_version": 1,
                                 "gate_flags": [], "must_haves_missing": [],
                                 "coverage_score": 0.7, "base_score": 70.0,
                                 "eligible": True, "reasons": []},
                      "layer2": None, "critic": None, "preference_adjustments": [],
                      "final_score": 70.0, "fit_band": band, "confidence": "high",
                      "summary_rationale": "Covers 70%."},
            "findings": {"candidate_id": real, "role_title": "Finance Analyst",
                         "rubric_version": 1, "findings": []},
            "features": None, "layer2_enforcement": [],
        }), encoding="utf-8")
    (out / "private" / "candidate_map.json").write_text(json.dumps(pmap), encoding="utf-8")

    stamps = _stamps()
    meta = {
        "run_id": run_id, "role_title": "Finance Analyst", "jurisdiction": "IN",
        "policy_pack": "IN", "started_utc": "2026-07-17T00:00:00+00:00",
        "counts": {"scored": len(cids)}, "bands": {},
        "versions": {
            "rubric": f"{req_id}.rubric.v1.yaml (v1)",
            "policy_files": {"base": stamps["policy_base"],
                             "IN": content_hash(Path("policies/IN.yaml").read_bytes())[:12]},
            "scoring_model": stamps["scoring_model"] if stamp_current else "v0 (default)",
            "preferences": {"applied": []},
        },
    }
    (out / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return real_ids


def _publish(**edits):
    from resume_screener.scoring_model import canonical_dump, load_scoring_model

    data = canonical_dump(load_scoring_model())
    for path, value in edits.items():
        node = data
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return governance.publish_scoring_model(data, author="admin@x",
                                            rationale="calibration")


def _publish_bands(strong=85):
    return _publish(bands__strong_at=strong)


def _publish_total_cap(points=10):
    return _publish(adjustments__total_cap_points=points)


def _publish_batch_quality(strong=80):
    return _publish(batch_quality__strong_at=strong)


# --- drift detection -----------------------------------------------------------------


def test_fresh_run_has_no_drift(workspace):
    from resume_screener.api import service

    req_id = _mk_role()
    _mk_run(req_id)
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_id)
    drift = service.config_drift(service.config(), req, service.read_run_meta(req_id))
    assert not any(drift["changed"].values()) and drift["details"] == []
    assert service.role_staleness(service.config(), req) is None


def test_scoring_model_drift_is_itemized_with_author_and_rationale(workspace):
    from resume_screener.api import service

    req_id = _mk_role()
    _mk_run(req_id)
    _publish_bands(85)
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_id)
    drift = service.config_drift(service.config(), req, service.read_run_meta(req_id))
    assert drift["changed"]["scoring_model"] is True
    item = next(d for d in drift["details"] if d["area"] == "scoring_model")
    assert item["headline"] == "Scoring model v1 → v2"
    assert {"path": "bands.strong_at", "label": "“Strong match” starts at",
            "from": "88", "to": "85"} in item["changes"]
    pub = item["publishes"][0]
    assert pub["author"] == "admin@x" and pub["rationale"] == "calibration"


def test_missing_stamp_counts_as_drift_when_model_governed(workspace):
    from resume_screener.api import service

    req_id = _mk_role()
    _mk_run(req_id, stamp_current=False)  # stamped v0 (default), file says v1
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_id)
    drift = service.config_drift(service.config(), req, service.read_run_meta(req_id))
    assert drift["changed"]["scoring_model"] is True


def test_batch_quality_only_change_is_never_stale(workspace, client):
    """A batch_quality-only publish moves the model file hash but no candidate's
    score (it is a live batch aggregate) — so it must NOT drift/stale, and a
    decided requisition must stay unlocked (2026-07-20)."""
    from resume_screener.api import service

    req_id = _mk_role()
    cids = _mk_run(req_id)
    # decide first, so the "would-be-locked" path is what we're proving stays open
    r = client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": cids[0], "action": "forward"})
    assert r.status_code == 200
    _publish_batch_quality(80)
    req = next(r_ for r_ in service.requisitions(service.config()) if r_.req_id == req_id)
    drift = service.config_drift(service.config(), req, service.read_run_meta(req_id))
    assert drift["changed"]["scoring_model"] is False
    assert not any(drift["changed"].values())
    assert service.role_staleness(service.config(), req) is None
    # a second decision is not blocked by a stale gate
    r2 = client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                     json={"candidate_id": cids[0], "action": "discuss"})
    assert r2.status_code == 200


# --- the mode state machine ------------------------------------------------------------


def test_mode_auto_for_free_change_without_decisions(workspace):
    from resume_screener.api import service

    req_id = _mk_role()
    _mk_run(req_id)
    _publish_bands(85)  # bands = free re-band, no model calls
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_id)
    stale = service.role_staleness(service.config(), req)
    assert stale == {"mode": "auto", "changed": ["scoring_model"],
                     "summary": "The scoring model changed after these scores were produced."}


def test_mode_soft_for_paid_change_without_decisions(workspace):
    from resume_screener.api import service

    req_id = _mk_role()
    _mk_run(req_id)
    _publish_total_cap(10)  # total cap sits in the L2 cache key → model calls
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_id)
    assert service.role_staleness(service.config(), req)["mode"] == "soft"


def test_mode_locked_once_a_decision_exists(workspace, client):
    from resume_screener.api import service

    req_id = _mk_role()
    cids = _mk_run(req_id)
    # record a decision under the CURRENT rules (allowed), then publish a change
    r = client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": cids[0], "action": "forward"})
    assert r.status_code == 200
    _publish_bands(85)
    req = next(r_ for r_ in service.requisitions(service.config()) if r_.req_id == req_id)
    stale = service.role_staleness(service.config(), req)
    assert stale["mode"] == "locked"


# --- payload surfaces ---------------------------------------------------------------


def test_role_card_and_review_carry_staleness(workspace, client):
    req_id = _mk_role()
    _mk_run(req_id)
    _publish_total_cap(10)
    card = next(c for c in client.get("/api/roles", headers=_as("HRHead")).json()["roles"]
                if c["req_id"] == req_id)
    assert card["staleness"]["mode"] == "soft"
    review = client.get(f"/api/roles/{req_id}/review", headers=_as("HRHead")).json()
    assert review["staleness"]["mode"] == "soft"
    assert any(d["area"] == "scoring_model" for d in review["staleness"]["details"])


def test_review_staleness_null_when_fresh(workspace, client):
    req_id = _mk_role()
    _mk_run(req_id)
    review = client.get(f"/api/roles/{req_id}/review", headers=_as("HRHead")).json()
    assert review["staleness"] is None


def test_preflight_reports_scoring_axis_and_stale(workspace, client):
    req_id = _mk_role()
    _mk_run(req_id)
    pre = client.get(f"/api/roles/{req_id}/screen/preflight", headers=_as("HRHead")).json()
    assert pre["reason"] == "nothing_changed" and pre["stale"] is None
    _publish_bands(85)
    pre = client.get(f"/api/roles/{req_id}/screen/preflight", headers=_as("HRHead")).json()
    assert pre["reason"] == "config_changed"
    assert pre["changed"]["scoring_model"] is True
    assert pre["stale"]["mode"] == "auto"
    assert any(d["area"] == "scoring_model" for d in pre["details"])
