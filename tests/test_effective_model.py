"""The model as it applies in one jurisdiction (global rollout, 2026-09-12).

THE CLAIM THIS SUITE DEFENDS. There is ONE scoring model for the organisation.
Everything structural — bands, clamp, coverage credit, the total adjustment cap,
the preference cap, confidence, batch quality — is identical in every region, and
a policy pack has no field that can move any of it. What a pack decides is which
signals fire there, how far each may move a score, and the market settings behind
them; and it may ADD never-scored topics, never remove one.

So two jurisdictions cannot conflict with each other. The only question is
model-versus-pack, and the first test here is the one that matters: the view is
derived from the SAME call a screening run makes, so it cannot drift from what a
run would actually do. A view that drifts is worse than no view.

Offline: real `policies/` and the real committed scoring model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resume_screener import effective_model
from resume_screener.policy import PolicyError, resolve_policy
from resume_screener.registry import apply_policy, load_registry
from resume_screener.scoring_model import load_scoring_model

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICIES = REPO_ROOT / "policies"
SIGNALS = REPO_ROOT / "signals"

PACKS = sorted(p.stem for p in POLICIES.glob("*.yaml") if p.stem != "base")


def view(jurisdiction: str | None = None) -> dict:
    return effective_model.effective_view(jurisdiction, POLICIES, SIGNALS)


def _signal(v: dict, signal_id: str) -> dict:
    return next(s for s in v["signals"] if s["id"] == signal_id)


# --- the view IS the run ------------------------------------------------------------


@pytest.mark.parametrize("code", PACKS)
def test_the_view_matches_what_a_run_would_actually_use(code):
    """Every enabled row, its cap and its params, against registry.apply_policy —
    the call the screening pipeline makes. This is the whole contract."""
    v = view(code)
    enabled, _excluded = apply_policy(
        load_registry(SIGNALS), resolve_policy(code, POLICIES))

    # params can hold a list (the base pack's empty `organizations`), so compare
    # them as text rather than as tuples
    def shape(sid, cap, params):
        return (sid, cap, json.dumps(params or {}, sort_keys=True))

    from_run = {shape(s.id, s.cap_points, s.params) for s in enabled}
    from_view = {shape(s["id"], s["effective_cap"], s["params"])
                 for s in v["signals"] if s["enabled_here"]}
    assert from_view == from_run


# --- structure belongs to the model, everywhere -------------------------------------


@pytest.mark.parametrize("code", PACKS)
def test_nothing_structural_differs_between_jurisdictions(code):
    assert view(code)["structural"] == view()["structural"]


def test_the_structural_block_says_it_is_global():
    assert view()["structural"]["scope"] == "global"


def test_every_jurisdiction_runs_the_same_model_version():
    stamps = {view(code)["model"]["stamp"] for code in PACKS}
    assert len(stamps) == 1


def test_the_total_adjustment_cap_is_the_models_alone():
    """No pack field can move it, so it reads the same everywhere."""
    caps = {view(code)["structural"]["adjustments"]["total_cap_points"]
            for code in PACKS}
    assert caps == {view()["structural"]["adjustments"]["total_cap_points"]}


# --- the dial: what a pack actually decides -----------------------------------------


def test_a_pack_can_raise_a_cap_above_the_models_and_it_is_marked():
    """India raises `language_evidence` from the model's cap. Allowed on purpose —
    a REPLACE, not a minimum — and marked, because it is the one direction a
    reader would not assume."""
    row = _signal(view("IN"), "language_evidence")
    assert row["effective_cap"] > row["model_cap"]
    assert row["cap_raised"] is True
    assert row["cap_origin"] == "pack"


def test_a_pack_can_turn_a_signal_off_and_says_which_pack_did():
    row = _signal(view("EU"), "language_evidence")
    assert row["enabled_here"] is False
    assert row["enabled_in_model"] is True      # the model ships it on
    assert "EU" in row["disabled_reason"]


def test_a_signal_the_model_ships_off_reads_as_off_in_the_model():
    row = _signal(view(), "public_work_relevance")
    assert row["enabled_here"] is False
    assert row["disabled_reason"] == "off in the global model"


def test_market_parameters_name_the_pack_that_set_each_one():
    """Params deep-merge, so "short stint = 9 months" (India) and
    "2 stints" (base) are both live values of the same knob."""
    row = _signal(view("IN"), "tenure_pattern_vs_stability")
    assert row["param_origins"]["short_stint_months"] == "IN"
    assert row["param_origins"]["min_stints_for_pattern"] == "base"


def test_an_untouched_signal_reads_as_the_models_own():
    row = _signal(view("SG"), "quota_attainment_evidence")
    assert row["origin"] == "model"
    assert row["effective_cap"] == row["model_cap"]


# --- protections only add up --------------------------------------------------------


def test_the_universal_floor_is_listed_by_name_in_every_jurisdiction():
    floor = view("EU")["critic"]["universal_floor"]
    assert floor and all(p["name"] and p["reason"] for p in floor)
    assert {p["id"] for p in floor} == {p["id"] for p in view("IN")["critic"]["universal_floor"]}


def test_a_jurisdiction_adds_its_own_blocked_factor_and_the_floor_keeps_its_own():
    """California's salary-history rule is `floor: true` in its own pack — Labor
    Code 432.3 is not advisory there — so "is it flagged floor?" is the wrong
    question. What this jurisdiction ADDS is what the base pack does not carry."""
    ca = view("US-CA")["critic"]
    added = {p["id"] for p in ca["pack_blocked_patterns"]}
    assert "ca_salary_history" in added
    assert added.isdisjoint({p["id"] for p in ca["universal_floor"]})


def test_a_pattern_that_is_not_a_scoring_stage_screen_says_so():
    """A blocked factor reaches the critic's deterministic screen only when it is
    scoring/narrative-staged AND carries literal phrases. California's is checked
    when someone WRITES a preference or a checklist row — a different moment, and
    worth not implying."""
    row = next(p for p in view("US-CA")["critic"]["pack_blocked_patterns"]
               if p["id"] == "ca_salary_history")
    assert row["critic_screened"] is False
    assert "preferences" in row["stages"]


def test_no_jurisdiction_can_shorten_the_never_scored_list():
    baseline = len(view()["critic"]["model_topics"])
    for code in PACKS:
        assert view(code)["critic"]["total_never_scored"] >= baseline


# --- no silent defaults --------------------------------------------------------------


def test_an_unknown_jurisdiction_is_an_error_not_a_default():
    with pytest.raises(PolicyError):
        view("ZZ")


def test_a_us_state_without_its_own_pack_falls_back_and_says_so():
    v = view("US-TX")
    assert v["pack"] == "US-default"
    assert any("US-default" in note for note in v["notes"])


# --- the route -----------------------------------------------------------------------


def test_the_route_serves_the_view_and_refuses_an_unknown_code(tmp_path, monkeypatch):
    import shutil

    from fastapi.testclient import TestClient

    from tests.test_sso_identity import principal

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(POLICIES, tmp_path / "policies")
    shutil.copytree(SIGNALS, tmp_path / "signals")
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)

    from resume_screener.api.app import app

    client = TestClient(app)

    def as_role(role: str) -> dict:
        return {"X-MS-CLIENT-PRINCIPAL": principal(f"{role.lower()}@example.com", [role])}

    # Legal holds rules_view and is exactly who reads this
    ok = client.get("/api/scoring-model/effective?jurisdiction=EU", headers=as_role("Legal"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["pack"] == "EU"

    globally = client.get("/api/scoring-model/effective", headers=as_role("HRHead"))
    assert globally.status_code == 200
    assert globally.json()["scope"] == "global"

    bad = client.get("/api/scoring-model/effective?jurisdiction=ZZ", headers=as_role("HRHead"))
    assert bad.status_code == 400
    assert "ZZ" in bad.json()["detail"]

    # a recruiter without rules_view cannot read the governance surface
    assert client.get("/api/scoring-model/effective",
                      headers=as_role("TASpecialist")).status_code == 403
