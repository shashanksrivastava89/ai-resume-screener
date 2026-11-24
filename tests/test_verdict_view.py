"""Who sees the AI's verdict (InfoSec 2026-10-14; widened 2026-10-21).

`verdict_view` is the one capability that sees a candidate's band, score, written
summary, score math, evidence, strengths and watch-outs, gate results, batch quality
and the AI's ranking order. From 2026-10-14 only HR held it; since 2026-10-21 (owner's
decision) every role that can open a requisition's candidates does — Legal, an
administrator, the hiring manager on the candidates shared with them. What stays with
the requisition's team is the DECISIONS (tests/test_decision_visibility.py).

The capability stays, so a deployment can narrow the verdict again in rbac.yaml
without a release — and the deny-list walk below still runs against such a narrowed
file: it walks every surface that returns candidate data as a viewer without the
verdict and fails on ANY verdict key or any of the AI's sentences. A field added to a
builder later fails it until it is named in api/verdict.py.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
from tests.test_sso_identity import _as, principal

REPO_ROOT = Path(__file__).resolve().parents[1]
STEM = "treasury-analyst"
HM_EMAIL = "hiringmanager@example.com"   # matches _as("HiringManager")
TA_EMAIL = "taspecialist@example.com"    # matches _as("TASpecialist")

# The AI's words — none may reach a viewer without verdict_view.
QUOTE = "Managed month-end GL close for four entities"
RATIONALE = "Recent hands-on treasury work across two systems"
SUMMARY = "Covers most of the checklist with recent treasury evidence"

VERDICT_KEYS = {
    "band", "band_label", "score", "confidence", "fit_band", "final_score", "chips",
    "gates", "must_have_gaps", "math", "score_math", "verdict", "reqs", "strengths",
    "watchouts", "notes", "pref_notes", "flag", "flag_note", "flag_items",
    "gate_items", "gate_warn", "gate_rule", "sub", "req_summary", "bands",
    "band_badge", "score_bar", "highlights", "fairness_flag", "gates_failed",
    "gate_passers",
}
VERDICT_STRINGS = (QUOTE, RATIONALE, SUMMARY, "Strong match", "Possible match")

# name -> (score, band); the AI's order is Upkaar > Anita > Chitra.
CANDIDATES = {"Anita Rao": (74.0, "Possible"), "Upkaar Roach": (91.0, "Strong"),
              "Chitra Das": (41.0, "Not a fit")}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copyfile(REPO_ROOT / "rbac.yaml", tmp_path / "rbac.yaml")
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
    from resume_screener import feature_flags

    feature_flags.set_flag("hm_portal", None, True, author="sa@example.com")
    # Ask Claude ships off (2026-10-23); its bundle is one of the surfaces walked here.
    feature_flags.set_flag("ask_claude_handoff", None, True, author="sa@example.com")
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _record(cid: str, score: float, band: str) -> dict:
    return {
        "score": {
            "candidate_id": cid, "role_title": "Treasury Analyst", "jurisdiction": "IN",
            "fit_band": band, "final_score": score, "confidence": "high",
            "summary_rationale": SUMMARY,
            "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                       "must_haves_missing": [], "coverage_score": score / 100,
                       "base_score": score, "eligible": True, "reasons": []},
            "layer2": {"candidate_id": cid, "narrative": SUMMARY, "proposed_signals": [],
                       "adjustments": [{"signal_id": "skill_recency", "points": 2.0,
                                        "evidence": QUOTE, "rationale": RATIONALE}]},
            "critic": None, "preference_adjustments": [],
        },
        "findings": {"candidate_id": cid, "role_title": "Treasury Analyst",
                     "rubric_version": 1,
                     "findings": [{"requirement_id": "r01", "requirement_text": "GL accounting",
                                   "match": "full", "evidence": QUOTE, "note": None},
                                  {"requirement_id": "r02", "requirement_text": "Oracle EBS",
                                   "match": "none", "evidence": None, "note": None}]},
        "features": None, "layer2_enforcement": [],
    }


def _screened() -> dict[str, str]:
    """A screened requisition owned by the TA Specialist with the HM assigned.
    Returns {name: candidate_id}."""
    from resume_screener.api import service
    from resume_screener.cache import content_hash

    jd = Path("jds") / f"{STEM}.md"
    jd.write_text("# Treasury Analyst\n\nOwns treasury.", encoding="utf-8")
    save_req_meta(jd, icims_req_id="REQ-77", job_location="IN", status="active",
                  created_by=TA_EMAIL, hm={"email": HM_EMAIL, "name": "Rahul",
                                           "status": "invited"})
    Path("jds", f"{STEM}.rubric.v1.yaml").write_text(
        "role_title: Treasury Analyst\njd_ref: jds/" + STEM + ".md\nversion: 1\n"
        "requirements:\n"
        "  - {id: r01, text: GL accounting, type: skill, kind: must_have, weight: 1}\n"
        "  - {id: r02, text: Oracle EBS, type: skill, kind: nice_to_have, weight: 1}\n",
        encoding="utf-8")
    resumes = Path("data/roles") / STEM / "resumes"
    resumes.mkdir(parents=True)
    out = Path("output") / STEM
    (out / "candidates").mkdir(parents=True)
    (out / "private").mkdir(parents=True)
    ids: dict[str, str] = {}
    private: dict[str, str] = {}
    for name, (score, band) in CANDIDATES.items():
        f = resumes / f"{name}.pdf"
        f.write_bytes(f"pdf-{name}".encode())
        cid = service.cached_candidate_id(f)
        ids[name] = cid
        private[cid] = str(f.resolve())
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps(_record(cid, score, band)), encoding="utf-8")
    (out / "private" / "candidate_map.json").write_text(json.dumps(private), encoding="utf-8")
    stamps = service.current_config_stamps(service.config())
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": "run-1", "role_title": "Treasury Analyst", "jurisdiction": "IN",
        "started_utc": "2026-10-14T09:00:00+00:00",
        "counts": {"scored": 3}, "bands": {"Strong": 1, "Possible": 1, "Not a fit": 1},
        "versions": {"rubric": f"{STEM}.rubric.v1.yaml (v1)",
                     "policy_files": {"base": stamps["policy_base"],
                                      "IN": content_hash(
                                          Path("policies/IN.yaml").read_bytes())[:12]},
                     "scoring_model": stamps["scoring_model"],
                     "preferences": {"applied": []}}}), encoding="utf-8")
    return ids


def _decide(client, cid: str, action: str) -> None:
    r = client.post(f"/api/roles/{STEM}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": cid, "action": action, "reason": "Spoke to them"})
    assert r.status_code == 200, r.text


def _share(client) -> None:
    r = client.post(f"/api/roles/{STEM}/share-hm", headers=_as("TASpecialist"))
    assert r.status_code == 200, r.text


def _leaks(obj, path="$") -> list[str]:
    """Every verdict key (with a value) or AI sentence anywhere in a JSON tree."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in VERDICT_KEYS:
                found.append(f"{path}.{k}")
            if k == "batch_quality" and v is not None:
                found.append(f"{path}.batch_quality")
            found += _leaks(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _leaks(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        found += [f"{path} says {s!r}" for s in VERDICT_STRINGS if s in obj]
    return found


SUPER_ADMIN_ONLY = {"X-MS-CLIENT-PRINCIPAL": principal("admin@example.com", ["SuperAdmin"])}
VIEWERS = {"Legal": _as("Legal"), "SuperAdmin": SUPER_ADMIN_ONLY,
           "HiringManager": _as("HiringManager")}

# The 2026-10-14 matrix — the verdict for HR roles only. It MENTIONS verdict_view, so
# the build's wider grants do not reach it: this is how a deployment narrows it again.
NARROWED_RBAC = """\
roles:
  SuperAdmin: [recruiter, reqs_all, reqs_read, rules_view, rules_edit, scoring_edit,
               changes_approve, runs_audit, data_manage, prefs_own, prefs_approve,
               access_view, access_edit, flags_view, flags_edit, hm_take]
  HRHead: [recruiter, reqs_all, rules_view, rules_edit, scoring_edit, changes_approve,
           runs_audit, prefs_own, prefs_approve, verdict_view, decisions_all]
  Legal: [rules_view, rules_edit, scoring_edit, changes_approve, runs_audit, reqs_read]
  IT: []
  TASpecialist: [recruiter, prefs_own, verdict_view]
  HRSpecialist: [recruiter, prefs_own, verdict_view]
  HiringManager: [hm_take]
"""


def _narrow() -> None:
    Path("rbac.yaml").write_text(NARROWED_RBAC, encoding="utf-8")


@pytest.mark.parametrize("who", list(VIEWERS))
def test_every_role_that_opens_the_requisition_sees_the_verdict(client, who):
    ids = _screened()
    _decide(client, ids["Anita Rao"], "forward")
    _share(client)
    h = VIEWERS[who]
    review = client.get(f"/api/roles/{STEM}/review", headers=h).json()
    top = review["cards"][0]
    assert top["candidate_id"] == ids["Upkaar Roach"]            # the AI's order
    assert top["band"] == "Strong" and top["score"] == 91 and top["math"]
    detail = client.get(f"/api/roles/{STEM}/review/{ids['Upkaar Roach']}", headers=h).json()
    assert detail["reqs"][0]["evidence"] == QUOTE and detail["verdict"]


@pytest.mark.parametrize("who", list(VIEWERS))
def test_a_narrowed_deployment_still_keeps_the_verdict_out(client, who):
    ids = _screened()
    _decide(client, ids["Anita Rao"], "forward")
    _decide(client, ids["Chitra Das"], "not")
    _share(client)
    _narrow()
    h = VIEWERS[who]
    cid = ids["Anita Rao"]
    urls = ["/api/roles", f"/api/roles/{STEM}", f"/api/roles/{STEM}/review",
            f"/api/roles/{STEM}/review?blind=true", f"/api/roles/{STEM}/review/{cid}"]
    if who != "HiringManager":   # audit + exports are not HM surfaces
        urls += ["/api/runs", "/api/audit/feed", "/api/audit/reports",
                 f"/api/audit/candidates/{STEM}/{cid}/timeline",
                 f"/api/roles/{STEM}/decisions", f"/api/roles/{STEM}/screen/runs"]
    for url in urls:
        r = client.get(url, headers=h)
        assert r.status_code == 200, (who, url, r.status_code, r.text[:200])
        assert _leaks(r.json()) == [], (who, url)

    if who != "HiringManager":
        pack = client.get(f"/api/roles/{STEM}/audit", headers=h)
        if pack.status_code == 200:   # runs_audit holders only
            assert "## Bands" not in pack.text
        csv = client.get(f"/api/roles/{STEM}/shortlist/export.csv", headers=h)
        assert csv.status_code == 200, csv.text
        header = csv.text.splitlines()[0]
        for col in ("Band", "Score", "Confidence", "Top strengths", "Watch-outs"):
            assert col not in header.split(","), (who, col)
        assert QUOTE not in csv.text and "Possible" not in csv.text

    r = client.post(f"/api/roles/{STEM}/handoff", headers=h, json={})
    assert r.status_code == 403, (who, r.text)


@pytest.mark.parametrize("role", ["TASpecialist", "HRHead"])
def test_hr_roles_see_bands_scores_and_math(client, role):
    ids = _screened()
    review = client.get(f"/api/roles/{STEM}/review", headers=_as(role)).json()
    top = review["cards"][0]
    assert top["candidate_id"] == ids["Upkaar Roach"]           # the AI's order
    assert top["band"] == "Strong" and top["score"] == 91 and top["math"]
    detail = client.get(f"/api/roles/{STEM}/review/{ids['Upkaar Roach']}",
                        headers=_as(role)).json()
    assert detail["reqs"][0]["evidence"] == QUOTE and detail["score_math"] is not None
    assert client.post(f"/api/roles/{STEM}/handoff", headers=_as(role),
                       json={}).status_code == 200


def test_a_hiring_manager_sees_the_shared_candidates_never_the_pool(client):
    """The verdict on what is shared — but nothing that reads every candidate
    screened: no band tally, no batch quality, no Ask-Claude bundle."""
    ids = _screened()
    _decide(client, ids["Anita Rao"], "forward")
    _share(client)
    hm = _as("HiringManager")
    review = client.get(f"/api/roles/{STEM}/review", headers=hm).json()
    assert [c["name"] for c in review["cards"]] == ["Upkaar Roach", "Anita Rao", "Chitra Das"]
    anita = next(c for c in review["cards"] if c["name"] == "Anita Rao")
    assert anita["band"] == "Possible"
    assert anita["decided"] == "forward" and anita["decided_reason"] == "Spoke to them"
    assert review["batch_quality"] is None
    detail = client.get(f"/api/roles/{STEM}/review/{ids['Anita Rao']}", headers=hm).json()
    assert detail["decision"]["action"] == "forward" and detail["reqs"]
    assert "comments" in detail and "resume_media_type" in detail
    resume = client.get(f"/api/roles/{STEM}/candidates/{ids['Anita Rao']}/resume", headers=hm)
    assert resume.status_code == 200
    role = client.get(f"/api/roles/{STEM}", headers=hm).json()
    assert "bands" not in role and role.get("batch_quality") is None
    card = next(c for c in client.get("/api/roles", headers=hm).json()["roles"]
                if c["req_id"] == STEM)
    assert card.get("batch_quality") is None
    r = client.post(f"/api/roles/{STEM}/handoff", headers=hm, json={})
    assert r.status_code == 403 and "recruiting team" in r.json()["detail"]


def test_without_the_verdict_a_teams_list_is_ordered_by_decision_then_name(client):
    ids = _screened()
    _decide(client, ids["Chitra Das"], "forward")
    _decide(client, ids["Upkaar Roach"], "not")
    _share(client)
    _narrow()
    # The hiring manager is on the team (sees decisions) but, in the narrowed file,
    # not the verdict: their list follows the decisions, never the AI's ranking.
    review = client.get(f"/api/roles/{STEM}/review", headers=_as("HiringManager")).json()
    assert [c["name"] for c in review["cards"]] == ["Chitra Das", "Upkaar Roach", "Anita Rao"]
    assert [c["rank"] for c in review["cards"]] == [1, 2, 3]
    assert review["counts"] == {"total": 3}


def test_without_the_verdict_or_the_decisions_the_list_is_by_name(client):
    """Outside the team the decisions are gone BEFORE the verdict's neutral order runs,
    so that order cannot carry them either."""
    ids = _screened()
    _decide(client, ids["Chitra Das"], "forward")
    _decide(client, ids["Upkaar Roach"], "not")
    _narrow()
    review = client.get(f"/api/roles/{STEM}/review", headers=_as("Legal")).json()
    assert [c["name"] for c in review["cards"]] == ["Anita Rao", "Chitra Das", "Upkaar Roach"]
    blind = client.get(f"/api/roles/{STEM}/review?blind=true", headers=_as("Legal")).json()
    assert [c["name"] for c in blind["cards"]][:2] == ["Candidate 1", "Candidate 2"]


def test_an_administrator_with_an_hr_role_sees_the_verdict(client):
    ids = _screened()
    both = {"X-MS-CLIENT-PRINCIPAL": principal("admin@example.com", ["SuperAdmin", "HRHead"])}
    detail = client.get(f"/api/roles/{STEM}/review/{ids['Upkaar Roach']}", headers=both).json()
    assert detail["band"] == "Strong"


def test_the_band_report_follows_the_verdict(client):
    _screened()
    legal = client.get("/api/audit/reports", headers=_as("Legal")).json()
    assert "decision_mix_by_band" in {r["key"] for r in legal["reports"]}
    _narrow()
    legal = client.get("/api/audit/reports", headers=_as("Legal")).json()
    assert "decision_mix_by_band" not in {r["key"] for r in legal["reports"]}
    assert client.get("/api/audit/reports/decision_mix_by_band.csv",
                      headers=_as("Legal")).status_code == 403
    hr = client.get("/api/audit/reports", headers=_as("HRHead")).json()
    assert "decision_mix_by_band" in {r["key"] for r in hr["reports"]}
    assert client.get("/api/audit/reports/decision_mix_by_band.csv",
                      headers=_as("HRHead")).status_code == 200


def test_run_rows_keep_the_throughput_but_not_the_band_tally(client):
    _screened()
    _narrow()
    legal = client.get("/api/runs", headers=_as("Legal")).json()["runs"][0]["meta"]
    hr = client.get("/api/runs", headers=_as("HRHead")).json()["runs"][0]["meta"]
    assert "3 scored" in legal and "strong" not in legal
    assert "3 scored · 1 strong" in hr


def test_the_mcp_connector_follows_the_same_rule(client, monkeypatch):
    from resume_screener import feature_flags
    from resume_screener.api import mcp

    feature_flags.set_flag("ask_claude_mcp", None, True, "test-setup")
    ids = _screened()
    _decide(client, ids["Anita Rao"], "forward")
    _share(client)
    monkeypatch.setenv("DEV_USER", HM_EMAIL)
    monkeypatch.setenv("DEV_ROLE", "HiringManager")
    shortlist = mcp.review_shortlist(STEM)
    assert shortlist["candidates"][0]["name"] == "Upkaar Roach"      # the shared set, ranked
    assert shortlist["batch_quality"] is None
    anita = next(c for c in shortlist["candidates"] if c["name"] == "Anita Rao")
    assert anita["decision"] == "forward"                         # the HM is on the team
    assert not isinstance(mcp.shortlist_chart(STEM), dict)        # a picture, not an error
    assert "recruiting team" in mcp.ask_claude_bundle(STEM)["error"]   # pool-wide

    monkeypatch.setenv("DEV_USER", TA_EMAIL)
    monkeypatch.setenv("DEV_ROLE", "TASpecialist")
    ranked = mcp.review_shortlist(STEM)
    assert ranked["candidates"][0]["name"] == "Upkaar Roach"
    assert ranked["candidates"][0]["band"] == "Strong match"
