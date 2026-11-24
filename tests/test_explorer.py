"""P45: the Audit Explorer backend — the queryable feed, per-candidate timelines,
rules log, retention/purge queue and CSV reports.

Guardrails pinned here: runs_audit gate (HR Head + IT only), region= filter,
`audit_view` emitted by EVERY handler ("viewing is itself logged") and excluded
from the feed by default, and the timeline built ONLY from synthetic tmp
artifacts (never real output/).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener import events, governance
from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import load_events
from tests.packdata import pack_days, plus_days
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

_HUMAN = {"forward": "agree", "discuss": "rerank", "not": "override"}
_BAND = {"forward": "Strong", "discuss": "Possible", "not": "Not a fit"}


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
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _screened_role(stem="finance-analyst", location="IN", run_id="run-1", cid="c1abc",
                   name_file="Anita Rao.pdf", started="2026-06-01T09:00:00+00:00",
                   decide=None, reason="strong treasury depth"):
    """One screened role with synthetic run artifacts (private map, candidate record,
    run_meta) and, optionally, one decision. `decide` is a forward/discuss/not action."""
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    save_req_meta(jd, job_location=location, icims_req_id=f"REQ-{stem}",
                  status="active", created_by="ta@example.com")

    out = Path("output") / stem
    (out / "private").mkdir(parents=True)
    (out / "private" / "candidate_map.json").write_text(
        json.dumps({cid: f"data/roles/{stem}/resumes/{name_file}"}), encoding="utf-8")
    (out / "candidates").mkdir(parents=True)
    (out / "candidates" / f"{cid}.json").write_text(json.dumps({
        "score": {"candidate_id": cid, "role_title": "Finance Analyst", "jurisdiction": location,
                  "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                             "must_haves_missing": [], "coverage_score": 0.82, "base_score": 82.0,
                             "eligible": True, "reasons": []},
                  "layer2": None, "critic": None, "final_score": 82.0, "fit_band": "Strong",
                  "confidence": "high", "summary_rationale": "ok"},
        "findings": {"candidate_id": cid, "role_title": "Finance Analyst", "rubric_version": 1,
                     "findings": [{"requirement_id": "r01", "requirement_text": "US GAAP",
                                   "match": "full", "evidence": "led US GAAP reporting", "note": ""}]},
        "suspended_adjustments": [],
    }), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": run_id, "started_utc": started, "jurisdiction": location, "policy_pack": location,
        "role_title": "Finance Analyst", "counts": {"scored": 1}, "bands": {"Strong": 1},
        "versions": {"rubric": "v1"},
    }), encoding="utf-8")

    if decide:
        path = Path("output/feedback/decisions.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "candidate_id": cid, "run_id": run_id, "suggestion": "Strong (82)",
                "human_action": _HUMAN[decide], "final_band": _BAND[decide], "reason": reason,
                "reviewer": "ta@example.com", "versions": {}, "ts": "2026-06-05T10:00:00+00:00",
            }) + "\n")
    return stem, cid


# --- feed --------------------------------------------------------------------------


def test_feed_composition_and_kind_filter(client):
    stem, _cid = _screened_role(decide="forward")
    governance.append_audit({"action": "publish_pack", "author": "hrhead@example.com",
                             "rationale": "tune tenure", "summary": "IN: signal settings",
                             "version": 2, "jurisdiction": "IN"})
    events.emit_event("req_close", actor="ta@example.com", req_id=stem, summary="Closed — hired 1")

    resp = client.get("/api/audit/feed", headers=_as("HRHead")).json()
    kinds = {e["kind"] for e in resp["entries"]}
    assert {"Decision", "Rules", "Close", "Data"} <= kinds
    assert resp["kinds"] == ["Decision", "Preference", "Rules", "Close", "Data", "Views"]

    decision = next(e for e in resp["entries"] if e["kind"] == "Decision")
    assert "strong treasury depth" in decision["summary"]  # VERBATIM reason
    assert "Anita Rao" in decision["summary"]

    only = client.get("/api/audit/feed?kind=Decision", headers=_as("HRHead")).json()
    assert only["entries"] and all(e["kind"] == "Decision" for e in only["entries"])


def test_a_preference_event_shows_its_text_but_the_event_line_does_not(client):
    """2026-10-16 (pen-test RS-01): every attempt is on record, with what it said —
    resolved from the preference store at read time, never copied into events."""
    from resume_screener import preferences

    text = "prefers candidates with no career gaps"
    rec = preferences.add_preference("ta@example.com", text, actor_role="TASpecialist")
    raw = Path("signals/events.jsonl").read_text(encoding="utf-8")
    assert rec["pref_id"] in raw and text not in raw
    added = next(e for e in load_events(kinds=["pref_add"]))
    assert added["data"]["deterministic_level"] == "warn"
    assert added["data"]["basis_ids"] and added["data"]["semantic"] == "off"

    feed = client.get("/api/audit/feed?kind=Preference", headers=_as("HRHead")).json()
    assert any(text in e["summary"] for e in feed["entries"])
    found = client.get("/api/audit/feed?q=career%20gaps", headers=_as("HRHead")).json()
    assert found["entries"] and all("career gaps" in e["summary"] for e in found["entries"])


def test_views_excluded_by_default(client):
    _screened_role()
    # every GET emits an audit_view event; the default feed must NOT surface them
    client.get("/api/audit/feed", headers=_as("HRHead"))
    default = client.get("/api/audit/feed", headers=_as("HRHead")).json()
    assert all(e["kind"] != "Views" for e in default["entries"])
    views = client.get("/api/audit/feed?kind=Views", headers=_as("HRHead")).json()
    assert views["entries"] and all(e["kind"] == "Views" for e in views["entries"])


def test_feed_requires_runs_audit(client):
    _screened_role()
    # a recruiter (TA Specialist) holds no runs_audit capability
    assert client.get("/api/audit/feed", headers=_as("TASpecialist")).status_code == 403


# --- every handler logs a view -----------------------------------------------------


def test_every_endpoint_logs_audit_view(client):
    stem, cid = _screened_role(decide="forward")
    client.get("/api/audit/feed", headers=_as("HRHead"))
    client.get("/api/audit/candidates?q=anita", headers=_as("HRHead"))
    client.get(f"/api/audit/candidates/{stem}/{cid}/timeline", headers=_as("HRHead"))
    client.get("/api/audit/requisitions", headers=_as("HRHead"))
    client.get("/api/audit/rules", headers=_as("HRHead"))
    client.get("/api/audit/retention", headers=_as("HRHead"))
    client.get("/api/audit/reports", headers=_as("HRHead"))
    client.get("/api/audit/reports/throughput_by_region.csv", headers=_as("HRHead"))

    scopes = {(e.get("data") or {}).get("scope")
              for e in load_events(kinds=["audit_view"])}
    assert {"feed", "candidates", "candidate_timeline", "requisitions", "rules",
            "retention", "reports", "report:throughput_by_region"} <= scopes


# --- candidate search + timeline ---------------------------------------------------


def test_candidate_search_and_timeline(client):
    stem, cid = _screened_role(decide="forward")
    matches = client.get("/api/audit/candidates?q=anita", headers=_as("HRHead")).json()["matches"]
    assert any(m["req_id"] == stem and m["candidate_id"] == cid and m["name"] == "Anita Rao"
               for m in matches)
    # empty query returns nothing (the roster isn't dumped)
    assert client.get("/api/audit/candidates?q=", headers=_as("HRHead")).json()["matches"] == []

    tl = client.get(f"/api/audit/candidates/{stem}/{cid}/timeline",
                    headers=_as("HRHead")).json()
    steps = {s["step"]: s for s in tl["steps"]}
    assert "screened" in steps and "decision" in steps and "handed_off" in steps
    assert steps["decision"]["reason"] == "strong treasury depth"  # VERBATIM
    assert tl["targets"]["audit_pack_url"] == f"/api/roles/{stem}/audit"
    assert tl["name"] == "Anita Rao"


def test_timeline_404_for_unknown_candidate(client):
    stem, _cid = _screened_role()
    assert client.get(f"/api/audit/candidates/{stem}/nope/timeline",
                      headers=_as("HRHead")).status_code == 404


# --- region filter -----------------------------------------------------------------


def test_region_filter(client):
    in_stem, _c1 = _screened_role("in-role", location="IN", run_id="run-in", cid="cin")
    ca_stem, _c2 = _screened_role("ca-role", location="US-CA", run_id="run-ca", cid="cca",
                                  name_file="Bob Lee.pdf")

    amer = client.get("/api/audit/feed?region=AMER", headers=_as("HRHead")).json()["entries"]
    assert any(e.get("req_id") == ca_stem for e in amer)
    assert not any(e.get("req_id") == in_stem for e in amer)


# --- rules + retention -------------------------------------------------------------


def test_rules_log_carries_rationale(client):
    governance.append_audit({"action": "publish_registry", "author": "hrhead@example.com",
                             "rationale": "added a job-related signal", "summary": "registry",
                             "version": 3})
    entries = client.get("/api/audit/rules", headers=_as("HRHead")).json()["entries"]
    assert any(e["rationale"] == "added a job-related signal" for e in entries)


def test_retention_view_queue_and_summaries(client):
    _screened_role("closed-role", location="IN")
    save_req_meta(Path("jds/closed-role.md"), status="closed",
                  closed_at="2026-01-01T00:00:00+00:00")
    resp = client.get("/api/audit/retention", headers=_as("HRHead")).json()
    row = next(r for r in resp["queue"] if r["req_id"] == "closed-role")
    assert row["purge_due"] == plus_days("2026-01-01T00:00:00+00:00", pack_days("IN")[1])
    assert any("candidate PII purged" in s and "IN" in s for s in resp["summaries"])


def test_hold_and_release_via_api(client):
    _screened_role("closed-role", location="IN")
    save_req_meta(Path("jds/closed-role.md"), status="closed",
                  closed_at="2026-01-01T00:00:00+00:00")
    r = client.post("/api/audit/retention/closed-role/hold", headers=_as("HRHead"),
                    json={"reason": "litigation hold"})
    assert r.status_code == 200
    row = next(rr for rr in client.get("/api/audit/retention", headers=_as("HRHead")).json()["queue"]
               if rr["req_id"] == "closed-role")
    assert row["status"] == "on_hold"
    # a hold with no reason is rejected
    assert client.post("/api/audit/retention/closed-role/hold", headers=_as("HRHead"),
                       json={"reason": ""}).status_code == 400
    assert client.post("/api/audit/retention/closed-role/release",
                       headers=_as("HRHead")).status_code == 200


# --- reports -----------------------------------------------------------------------


def test_reports_catalog_and_csv_shapes(client):
    _screened_role(decide="forward")

    catalog = client.get("/api/audit/reports", headers=_as("HRHead")).json()["reports"]
    by_key = {r["key"]: r for r in catalog}
    assert by_key["adverse_impact"]["status"] == "Phase 2"
    assert {"throughput_by_region", "decision_mix_by_band", "time_to_shortlist"} <= set(by_key)

    tp = client.get("/api/audit/reports/throughput_by_region.csv", headers=_as("HRHead"))
    assert tp.headers["content-type"].startswith("text/csv")
    assert tp.text.splitlines()[0] == "region,runs"

    mix = client.get("/api/audit/reports/decision_mix_by_band.csv", headers=_as("HRHead"))
    assert mix.text.splitlines()[0] == "band,move_forward,discuss,not_a_fit"

    tts = client.get("/api/audit/reports/time_to_shortlist.csv", headers=_as("HRHead"))
    assert tts.text.splitlines()[0] == "req_id,title,run_started,last_decision,days"

    # the Phase-2 report has no CSV yet
    assert client.get("/api/audit/reports/adverse_impact.csv",
                      headers=_as("HRHead")).status_code == 400
