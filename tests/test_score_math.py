"""P51 score-math explainability: the compact card math re-adds to the stored
final, the full trace uses the RUN's rubric/scoring-model versions (as-of), and
no résumé evidence text leaks into the math payload."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resume_screener.api import score_math
from tests.test_staleness import _as, _mk_role, _mk_run, _publish_bands, client, workspace  # noqa: F401


def _rec(base=70.0, final=76.0, adj=None, suspended=None, prefs=None,
         must_missing=(), findings=None):
    adj = adj or []
    return {
        "score": {
            "candidate_id": "cid", "role_title": "R", "jurisdiction": "IN",
            "layer1": {"candidate_id": "cid", "rubric_version": 1, "gate_flags": [],
                       "must_haves_missing": list(must_missing),
                       "coverage_score": base / 100, "base_score": base,
                       "eligible": True, "reasons": []},
            "layer2": {"candidate_id": "cid", "adjustments": adj,
                       "proposed_signals": [], "narrative": "n"} if adj else None,
            "critic": {"verdict_per_adjustment": {a["signal_id"]: "pass" for a in adj},
                       "notes": [], "overall_flags": []} if adj else None,
            "preference_adjustments": prefs or [],
            "final_score": final, "fit_band": "Strong" if final >= 88 else
            ("Possible" if final >= 60 else "Not a fit"),
            "confidence": "high", "summary_rationale": "ok",
        },
        "findings": {"candidate_id": "cid", "role_title": "R", "rubric_version": 1,
                     "findings": findings or []},
        "suspended_adjustments": suspended or [],
        "layer2_enforcement": [],
        "extraction": {"confidence": 0.95, "extractor": "di", "pages": 1},
    }


def _adj(sid="skill_recency", pts=4.0):
    return {"signal_id": sid, "points": pts, "evidence": "seven years of Tally",
            "rationale": "recent hands-on"}


def test_brief_math_re_adds_to_the_stored_final():
    rec = _rec(base=70, final=75.0, adj=[_adj(pts=4.0)],
               prefs=[{"pref_id": "p1", "owner": "o", "text": "t", "points": 1.0,
                       "evidence": None, "rationale": "r"}])
    m = score_math.brief_math(rec)
    assert (m["base"], m["adjustments"], m["preferences"]) == (70.0, 4.0, 1.0)
    assert m["before_limits"] == 75.0 == m["final"] and m["clamped"] is None


def test_brief_math_excludes_suspended_and_marks_clamp():
    rec = _rec(base=98, final=100.0,
               adj=[_adj("a", 6.0), _adj("b", -2.0)],
               suspended=[{"signal_id": "b", "points": -2.0, "evidence": "e",
                           "rationale": "r"}])
    m = score_math.brief_math(rec)
    assert m["adjustments"] == 6.0  # b's -2 was set aside
    assert m["suspended_count"] == 1
    assert m["before_limits"] == 104.0 and m["clamped"] == "high"
    assert m["final"] == 100.0


def test_full_trace_uses_run_version_rubric_and_model(workspace):
    from resume_screener.api import service

    req_id = _mk_role()
    _mk_run(req_id)
    # a v2 checklist + a v2 scoring model land AFTER the run…
    Path("jds", f"{req_id}.rubric.v2.yaml").write_text(
        Path("jds", f"{req_id}.rubric.v1.yaml").read_text(encoding="utf-8")
        .replace("version: 1", "version: 2"), encoding="utf-8")
    _publish_bands(85)
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_id)
    rec = _rec(findings=[{"requirement_id": "r01", "requirement_text": "GL accounting",
                          "match": "partial", "evidence": "GL", "note": None}])
    meta = service.read_run_meta(req_id)
    trace = score_math.build_score_math(req, rec, meta)
    # …but the trace explains the RUN: model v1 (bands 88/60), rubric v1 weights.
    assert trace["as_of"]["scoring_model_version"] == 1
    assert trace["as_of"]["approximate"] is False
    assert trace["band"]["strong_at"] == 88
    line = trace["base"]["lines"][0]
    assert line == {"label": "GL accounting", "kind": "must_have", "weight_pct": 100.0,
                    "credit": "partial", "credit_value": 0.5, "points": 50.0}


def test_no_evidence_text_in_score_math():
    rec = _rec(adj=[_adj(pts=3.0)],
               findings=[{"requirement_id": "r01", "requirement_text": "GL",
                          "match": "full", "evidence": "SECRET-QUOTE", "note": None}])
    trace = score_math.build_score_math(_FakeReq(), rec, None)
    assert "SECRET-QUOTE" not in json.dumps(trace)
    assert "seven years of Tally" not in json.dumps(trace)


class _FakeReq:
    req_id = "none-such"
    job_location = "IN"
    jd_ref = "jds/none.md"


def test_detail_and_cards_carry_math(workspace, client):
    req_id = _mk_role()
    cids = _mk_run(req_id)
    review = client.get(f"/api/roles/{req_id}/review", headers=_as("HRHead")).json()
    card = review["cards"][0]
    assert card["math"]["base"] == 70.0 and card["math"]["final"] == 70.0
    detail = client.get(f"/api/roles/{req_id}/review/{cids[0]}",
                        headers=_as("HRHead")).json()
    sm = detail["score_math"]
    assert sm["total"]["final"] == 70.0
    assert sm["band"]["strong_at"] == 88 and sm["band"]["possible_at"] == 60
    assert sm["confidence"]["level"] == "high"
