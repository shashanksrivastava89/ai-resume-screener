"""Phase 3 (2026-07-21): the config-to-config cascade.

* scoring_conflicts — enabled signals that overlap a currently in-force
  jurisdiction BLOCKED pattern (the org scores what a region forbids). Only
  blocked severity counts; the real shipped model must be conflict-free.
* blocked_patterns_as_topics + the critic code screen — a jurisdiction's
  scoring/narrative-staged blocked factor suspends a Layer-2 adjustment whose
  evidence references it ("jurisdiction supersedes, auto-applied downstream").
* GET /api/scoring/conflicts surfaces the staleness for the Scoring page.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import cascade
from resume_screener.models import Adjustment, BlockedPattern
from resume_screener.scoring_model import load_scoring_model
from tests.test_sso_identity import principal

REPO_ROOT = Path(__file__).resolve().parents[1]


def _blocked(pid="ca_salary_history", severity="blocked", phrases=("salary history",), stages=None):
    return BlockedPattern(id=pid, name="Salary history", reason="California bars it.",
                          severity=severity, floor=False,
                          stages=list(stages or ["preferences", "checklist"]), phrases=list(phrases))


# --- the detector ---------------------------------------------------------------------


def test_real_shipped_model_is_conflict_free():
    # No shipped signal references a jurisdiction blocked factor — a false positive
    # here would wrongly flag a real scoring rule stale.
    assert cascade.scoring_conflicts() == []


def test_synthetic_conflict_is_detected():
    m = load_scoring_model()
    sig = m.signals[0].model_copy(update={
        "id": "pay_progression", "display_name": "Pay progression",
        "definition": "Rewards a strong salary history and pay growth."})
    model = m.model_copy(update={"signals": [sig]})
    out = cascade.scoring_conflicts_for(model, [("US-CA", [_blocked()])])
    assert len(out) == 1
    assert out[0]["signal_id"] == "pay_progression" and out[0]["jurisdiction"] == "US-CA"


def test_only_blocked_severity_conflicts():
    m = load_scoring_model()
    sig = m.signals[0].model_copy(update={
        "id": "pay_progression", "definition": "Considers salary history."})
    model = m.model_copy(update={"signals": [sig]})
    warn = _blocked(severity="warn")
    assert cascade.scoring_conflicts_for(model, [("US-CA", [warn])]) == []


def test_retired_signal_is_not_a_conflict():
    m = load_scoring_model()
    sig = m.signals[0].model_copy(update={
        "id": "pay_progression", "definition": "Considers salary history.", "retired": True})
    model = m.model_copy(update={"signals": [sig]})
    assert cascade.scoring_conflicts_for(model, [("US-CA", [_blocked()])]) == []


# --- critic-stage enforcement ---------------------------------------------------------


def test_blocked_patterns_as_topics_only_scoring_narrative_with_phrases():
    # prefs/checklist-staged -> skipped (classify_text handles those stages)
    assert cascade.blocked_patterns_as_topics([_blocked(stages=["preferences", "checklist"])]) == []
    # regex-only scoring-staged -> skipped (TopicRule has no regex)
    rx = BlockedPattern(id="x", name="X", reason="r", severity="blocked",
                        stages=["scoring"], regexes=[r"\bfoo\b"])
    assert cascade.blocked_patterns_as_topics([rx]) == []
    # scoring-staged with phrases -> one TopicRule namespaced jurisdiction:*
    topics = cascade.blocked_patterns_as_topics([_blocked(stages=["scoring", "narrative"])])
    assert len(topics) == 1 and topics[0].id == "jurisdiction:ca_salary_history"
    assert "scoring" in topics[0].stages


def test_critic_code_screen_catches_jurisdiction_block_in_evidence():
    from resume_screener.critic import code_screen

    topics = cascade.blocked_patterns_as_topics([_blocked(stages=["scoring"])])
    adj = Adjustment(signal_id="quota_attainment_evidence", points=2.0,
                     evidence="Strong salary history through three promotions.",
                     rationale="Consistent pay growth.")
    assert "jurisdiction:ca_salary_history" in code_screen(adj, topics)


# --- API surface ----------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "signals").mkdir()
    for name in ("features.yaml", "scoring_model.v1.yaml"):
        shutil.copyfile(REPO_ROOT / "signals" / name, tmp_path / "signals" / name)
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app
    return TestClient(app)


def test_conflicts_endpoint_shape_and_auth(client):
    def _as(role):
        return {"X-MS-CLIENT-PRINCIPAL": principal(f"{role.lower()}@example.com", [role])}
    r = client.get("/api/scoring/conflicts", headers=_as("HRHead"))
    assert r.status_code == 200 and r.json() == {"conflicts": []}  # shipped model is clean
    # rules_view is required
    assert client.get("/api/scoring/conflicts", headers=_as("TASpecialist")).status_code == 403
