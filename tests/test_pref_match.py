"""P40: the preference-matcher's enforcement — offline (parse_structured faked).

Proves the CODE half is the guardrail: unknown ids and unverifiable evidence are
dropped, points clamp to ±2, a since-blocked preference contributes 0, and the
cache key includes the prompt version (so a prompt bump re-runs).
"""

from __future__ import annotations

import pytest

from resume_screener import azure_clients, pref_match
from resume_screener.cache import JsonCache
from resume_screener.findings import FindingsRecord
from resume_screener.models import Finding, ResumeProfile

RESUME = "Led GST and TDS filings for a listed manufacturer. Built Power BI dashboards."


def _profile() -> ResumeProfile:
    return ResumeProfile(candidate_id="cid", source_file="x.pdf", redacted_text=RESUME)


def _findings() -> FindingsRecord:
    return FindingsRecord(
        candidate_id="cid", role_title="Role", rubric_version=1,
        findings=[Finding(requirement_id="r01", requirement_text="GST", match="full",
                          evidence="GST and TDS filings")],
    )


def _fake_matches(monkeypatch, matches):
    """Make parse_structured return a PrefMatchList of the given raw drafts,
    without any network call."""
    def fake(_client, _model, _messages, _model_cls):
        return pref_match.PrefMatchList(
            matches=[pref_match.PrefMatchDraft(**m) for m in matches])
    monkeypatch.setattr(azure_clients, "parse_structured", fake)


def test_evidence_must_be_verbatim_or_dropped(tmp_path, monkeypatch):
    _fake_matches(monkeypatch, [
        {"pref_id": "p1", "matched": True, "points": 2.0,
         "evidence": "GST and TDS filings", "rationale": "does GST"},          # verbatim -> kept
        {"pref_id": "p2", "matched": True, "points": 2.0,
         "evidence": "managed a huge finance team", "rationale": "made up"},    # not in text -> dropped
    ])
    prefs = [{"pref_id": "p1", "owner": "o", "text": "prefers GST depth"},
             {"pref_id": "p2", "owner": "o", "text": "prefers team leadership"}]
    adj, cached = pref_match.run_pref_match(
        _profile(), _findings(), prefs, client=None, model="m", cache=JsonCache(tmp_path))
    assert cached is False
    assert [a.pref_id for a in adj] == ["p1"]
    assert adj[0].points == 2.0 and adj[0].evidence == "GST and TDS filings"


def test_unknown_pref_id_dropped(tmp_path, monkeypatch):
    _fake_matches(monkeypatch, [
        {"pref_id": "ghost", "matched": True, "points": 2.0,
         "evidence": "Power BI dashboards", "rationale": "x"},
    ])
    prefs = [{"pref_id": "p1", "owner": "o", "text": "prefers BI tooling"}]
    adj, _ = pref_match.run_pref_match(
        _profile(), _findings(), prefs, client=None, model="m", cache=JsonCache(tmp_path))
    assert adj == []


def test_points_clamped_to_two(tmp_path, monkeypatch):
    _fake_matches(monkeypatch, [
        {"pref_id": "p1", "matched": True, "points": 9.0,
         "evidence": "Power BI dashboards", "rationale": "x"},
    ])
    prefs = [{"pref_id": "p1", "owner": "o", "text": "prefers BI tooling"}]
    adj, _ = pref_match.run_pref_match(
        _profile(), _findings(), prefs, client=None, model="m", cache=JsonCache(tmp_path))
    assert adj[0].points == 2.0


def test_unmatched_and_zero_dropped(tmp_path, monkeypatch):
    _fake_matches(monkeypatch, [
        {"pref_id": "p1", "matched": False, "points": 0.0, "evidence": None, "rationale": ""},
        {"pref_id": "p2", "matched": True, "points": 0.0,
         "evidence": "Power BI dashboards", "rationale": "x"},
    ])
    prefs = [{"pref_id": "p1", "owner": "o", "text": "a"},
             {"pref_id": "p2", "owner": "o", "text": "b"}]
    adj, _ = pref_match.run_pref_match(
        _profile(), _findings(), prefs, client=None, model="m", cache=JsonCache(tmp_path))
    assert adj == []


def test_a_preference_can_never_subtract(tmp_path, monkeypatch):
    """2026-09-25: "Never negative" was only an instruction to the model, and the code
    clamped to ±cap. A preference the fairness check passes as `ok` needs no approval,
    so a penalty-phrased one ("deduct if they can't work nights") plus a compliant
    model was an unreviewed deduction. Matched, evidenced, verbatim — and still 0."""
    _fake_matches(monkeypatch, [
        {"pref_id": "p1", "matched": True, "points": -2.0,
         "evidence": "Power BI dashboards", "rationale": "deduct as asked"},
    ])
    prefs = [{"pref_id": "p1", "owner": "o", "text": "deduct points for BI-only profiles"}]
    adj, _ = pref_match.run_pref_match(
        _profile(), _findings(), prefs, client=None, model="m", cache=JsonCache(tmp_path))
    assert adj == []


def test_since_blocked_pref_never_reaches_the_model(tmp_path, monkeypatch):
    # A preference whose text now classifies as BLOCKED is filtered before the call
    # and contributes 0 — even if the (faked) model would "match" it.
    called = {"n": 0}
    def fake(_c, _m, _msg, _cls):
        called["n"] += 1
        return pref_match.PrefMatchList(matches=[])
    monkeypatch.setattr(azure_clients, "parse_structured", fake)
    prefs = [{"pref_id": "p1", "owner": "o", "text": "prefers candidates under 30"}]  # blocked
    adj, cached = pref_match.run_pref_match(
        _profile(), _findings(), prefs, client=None, model="m", cache=JsonCache(tmp_path))
    assert adj == [] and cached is False
    assert called["n"] == 0  # no model call at all — the only live pref was blocked


def test_empty_pref_list_makes_no_call(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not call the model with no prefs")
    monkeypatch.setattr(azure_clients, "parse_structured", boom)
    adj, cached = pref_match.run_pref_match(
        _profile(), _findings(), [], client=None, model="m", cache=JsonCache(tmp_path))
    assert adj == [] and cached is False


def test_cache_key_includes_prompt_version(tmp_path, monkeypatch):
    _fake_matches(monkeypatch, [
        {"pref_id": "p1", "matched": True, "points": 2.0,
         "evidence": "Power BI dashboards", "rationale": "x"},
    ])
    prefs = [{"pref_id": "p1", "owner": "o", "text": "prefers BI tooling"}]
    cache = JsonCache(tmp_path)
    _, first_cached = pref_match.run_pref_match(_profile(), _findings(), prefs, None, "m", cache)
    _, second_cached = pref_match.run_pref_match(_profile(), _findings(), prefs, None, "m", cache)
    assert first_cached is False and second_cached is True  # same key -> cache hit
    # bump the prompt version -> a different key -> a fresh (uncached) call
    monkeypatch.setattr(pref_match, "PREFS_PROMPT_VERSION", "p40.test-bump")
    _, third_cached = pref_match.run_pref_match(_profile(), _findings(), prefs, None, "m", cache)
    assert third_cached is False
