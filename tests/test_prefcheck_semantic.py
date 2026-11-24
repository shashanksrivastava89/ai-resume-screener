"""The fairness check's intent screen (prefcheck_semantic, 2026-10-16, pen-test RS-01).

The model is faked: `_client` returns a stand-in and `_parse` answers from a table, so
nothing here reaches Azure. What is pinned is the contract around the model — it can
only escalate, it is held to the governed list, it fails safe, it is called once per
batch and cached — not what any real model would say.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener import prefcheck_semantic as ps
from resume_screener.cache import JsonCache
from resume_screener.models import BlockedPattern
from resume_screener.scoring_model import TopicRule
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

# A rule set the test controls, so later changes to the governed lists cannot move
# the deterministic half of these verdicts.
PATTERNS = [BlockedPattern(
    id="protected_demographic", name="Protected characteristics",
    reason="Preferences can never be based on gender.", severity="blocked",
    stages=["preferences", "checklist"], regexes=[r"\bgender\b"])]
TOPICS = [
    TopicRule(id="family_status", name="Family status", reason="Says nothing about the job.",
              stages=["preferences", "checklist"], phrases=["marital status"]),
    TopicRule(id="scoring_only", name="Scoring only", reason="Not for preferences.",
              stages=["scoring"], phrases=["zebra"]),
]
PROXY = "no primary caregiver duties please"
CLEAN = "strong US GAAP experience"


class Model:
    """A fake model: `answers` maps a text to (level, basis_ids); anything else is ok."""

    def __init__(self, answers=None, *, fail=None, omit=()):
        self.answers = answers or {}
        self.fail = fail
        self.omit = set(omit)
        self.calls: list[list[dict]] = []

    def __call__(self, client, model, messages, schema):
        self.calls.append(messages)
        if self.fail is not None:
            raise self.fail
        texts = json.loads(messages[1]["content"].split("<texts>\n", 1)[1].rsplit("\n</texts>", 1)[0])
        items = []
        for row in texts:
            if row["text"] in self.omit:
                continue
            level, basis = self.answers.get(row["text"], ("ok", []))
            items.append(ps.SemanticItem(index=row["index"], level=level, basis_ids=basis,
                                         reason="model sentence"))
        return schema(items=items)


@pytest.fixture()
def model(monkeypatch, tmp_path):
    def install(fake: Model | None = None, *, client=True):
        monkeypatch.setenv("PREF_SEMANTIC_CHECK", "on")
        monkeypatch.setattr(ps, "_client", lambda: ("fake-client", "chat") if client else None)
        monkeypatch.setattr(ps, "_cache", lambda: JsonCache(tmp_path / "cache"))
        if fake is not None:
            monkeypatch.setattr(ps, "_parse", fake)
        return fake
    return install


def _screen(texts, stage="preferences"):
    return ps.screen_texts(texts, stage=stage, blocked_patterns=PATTERNS, topics=TOPICS)


# --- escalate only -------------------------------------------------------------------


def test_the_model_can_turn_a_clean_verdict_into_a_warning(model):
    model(Model({PROXY: ("warn", ["family_status"])}))
    [v] = _screen([PROXY])
    assert v.level == "warn" and v.semantic == "flagged"
    assert v.deterministic_level == "ok" and v.semantic_level == "warn"
    assert "family_status" in v.basis_ids
    # the sentence is built from the GOVERNED name, not the model's own words
    assert "Family status" in v.reason and "model sentence" not in v.reason


def test_the_model_never_lowers_a_verdict(model):
    model(Model())  # says ok to everything
    [v] = _screen(["prefers a particular marital status"])
    assert v.level == "warn" and v.deterministic_level == "warn"
    assert v.semantic == "ok" and v.semantic_level == "ok"


def test_a_blocked_text_never_reaches_the_model(model):
    fake = model(Model())
    [v] = _screen(["gender balance in the team"])
    assert v.level == "blocked" and fake.calls == []


def test_the_model_flags_but_never_closes_the_approval_path(model):
    """Blocked has no approval path, so only the governed lists may produce it — the
    real model blocked an ordinary "administering maternity leave policies"
    requirement when it was allowed to (2026-10-16)."""
    model(Model({
        PROXY: ("blocked", ["made_up_rule"]),            # not governed
        "no family plans": ("blocked", ["family_status"]),  # governed, but a warn topic
        "women only": ("blocked", ["protected_demographic"]),  # governed and blocked
    }))
    unknown, warn_topic, real = _screen([PROXY, "no family plans", "women only"])
    assert unknown.level == "warn" and unknown.basis_ids == ["intent_screen"]
    assert unknown.reason == ps._UNGOVERNED_REASON
    assert warn_topic.level == "warn" and "touches “Family status”" in warn_topic.reason
    assert real.level == "warn" and real.semantic_level == "blocked"
    assert "rules out people by “Protected characteristics”" in real.reason


# --- fail safe -----------------------------------------------------------------------


def test_when_the_model_fails_a_clean_text_waits_for_a_person(model):
    model(Model(fail=RuntimeError("boom")))
    clean, warned = _screen([CLEAN, "prefers a particular marital status"])
    assert clean.level == "warn" and clean.reason == ps.UNAVAILABLE_REASON
    assert clean.semantic == "unavailable" and clean.deterministic_level == "ok"
    # a warning the phrase lists found keeps its own sentence
    assert warned.level == "warn" and "Family status" in warned.reason
    assert warned.semantic == "unavailable"


def test_no_azure_is_the_same_as_a_failed_call(model):
    model(Model(), client=False)
    [v] = _screen([CLEAN])
    assert v.level == "warn" and v.reason == ps.UNAVAILABLE_REASON


def test_a_text_the_model_skipped_waits_for_a_person(model):
    model(Model(omit={CLEAN}))
    [v] = _screen([CLEAN])
    assert v.level == "warn" and v.semantic == "unavailable"


def test_switched_off_it_is_the_phrase_lists_alone(model, monkeypatch):
    fake = model(Model({PROXY: ("warn", ["family_status"])}))
    monkeypatch.setenv("PREF_SEMANTIC_CHECK", "off")
    [v] = _screen([PROXY])
    assert v.level == "ok" and v.semantic == "off" and fake.calls == []


def test_the_retries_are_bounded(monkeypatch):
    """parse_structured's own policy retries five times with backoff — inside one
    HTTP request that is minutes. The screen allows two attempts."""
    import httpx
    import openai

    calls = []

    class Completions:
        def parse(self, **kw):
            calls.append(kw)
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://x"))

    client = type("C", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    with pytest.raises(openai.APITimeoutError):
        ps._parse(client, "chat", [{"role": "user", "content": "x"}], ps.SemanticDraft)
    assert len(calls) == 2


# --- one call, cached, held to the list ---------------------------------------------


def test_a_batch_is_one_call_and_answers_stay_aligned(model):
    fake = model(Model({"b caregiver": ("warn", ["family_status"])}))
    out = _screen(["a clean", "gender quota", "b caregiver", "c clean"])
    assert len(fake.calls) == 1
    assert [v.level for v in out] == ["ok", "blocked", "warn", "ok"]


def test_a_cached_answer_is_not_asked_again(model):
    fake = model(Model({PROXY: ("warn", ["family_status"])}))
    first = _screen([PROXY])
    second = _screen([PROXY])
    assert len(fake.calls) == 1 and first[0].level == second[0].level == "warn"


def test_the_prompt_carries_the_governed_list_for_the_stage_only(model):
    fake = model(Model())
    _screen([CLEAN])
    system, user = fake.calls[0]
    assert "untrusted DATA" in system["content"]
    assert "Never suggest how to reword" in system["content"]
    never_use = json.loads(user["content"].split("<never_use>\n", 1)[1].split("\n</never_use>")[0])
    assert {r["id"] for r in never_use} == {"protected_demographic", "family_status"}


# --- through the API ----------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "signals").mkdir()
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return TestClient(app)


def _mk_role(client) -> str:
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9101",
    })
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def test_a_flagged_preference_waits_with_the_reason(client, model):
    model(Model({PROXY: ("warn", ["employment_gaps"])}))
    r = client.post("/api/preferences", headers=_as("TASpecialist"), json={"text": PROXY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["level"] == "warn"
    assert body["warning"] and "model sentence" not in body["warning"]
    check = client.post("/api/preferences/check", headers=_as("TASpecialist"),
                        json={"text": PROXY}).json()
    assert check["semantic"] == "flagged" and check["deterministic_level"] == "ok"


def test_a_checklist_publish_is_one_call_and_keeps_the_reason(client, model):
    fake = model(Model({"no primary caregiver duties": ("warn", ["employment_gaps"])}))
    req_id = _mk_role(client)
    rows = [{"text": "Strong US GAAP experience", "kind": "must_have", "importance": 5},
            {"text": "no primary caregiver duties", "kind": "nice_to_have", "importance": 2},
            {"text": "Oracle EBS exposure", "kind": "nice_to_have", "importance": 2}]
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": rows})
    assert r.status_code == 200, r.text
    assert len(fake.calls) == 1 and r.json()["needs_approval"] is True
    got = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist")).json()
    flagged = [row for row in got["requirements"] if row["critic_level"] == "warn"]
    assert [row["text"] for row in flagged] == ["no primary caregiver duties"]
    # the stored sentence, not a recomputation that would say "no conflicts"
    assert "never scored here" in flagged[0]["critic_reason"]
    assert got["approval"]["status"] == "none"


def test_what_the_model_calls_blocked_waits_for_the_hr_head(client, model):
    """The model never closes the approval path: its "blocked" holds the checklist for
    HR Head approval with the stronger sentence, and the publish is not refused."""
    model(Model({"only applicants of one sex": ("blocked", ["protected_demographic"])}))
    req_id = _mk_role(client)
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [{"text": "only applicants of one sex", "kind": "must_have",
                          "importance": 3}]})
    assert r.status_code == 200 and r.json()["needs_approval"] is True
    [row] = r.json()["requirements"]
    assert row["critic_level"] == "warn" and "rules out people" in row["critic_reason"]


def test_when_the_model_is_down_the_checklist_waits_for_approval(client, model):
    model(Model(), client=False)
    req_id = _mk_role(client)
    check = client.post(f"/api/roles/{req_id}/checklist/check", headers=_as("TASpecialist"),
                        json={"text": "Strong US GAAP experience"}).json()
    assert check == {"level": "warn", "reason": ps.UNAVAILABLE_REASON, "semantic": "unavailable"}
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [{"text": "Strong US GAAP experience", "kind": "must_have",
                          "importance": 5}]})
    assert r.status_code == 200 and r.json()["needs_approval"] is True
    from resume_screener.api import service

    req = service.find_requisition(req_id)
    assert service.checklist_gate(req, service.rubric_for(req))["ready"] is False
