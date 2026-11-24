"""The regional dial, proposed and approved like anything else (2026-09-12).

A policy pack decides which of the model's AI signals fire in its jurisdiction,
how far each may move a score, and the market settings behind them. That is the
ONLY place regional variation in scoring lives — and it could be edited by hand
in YAML or through the API, but a proposal carried no validation of it and told
the approver nothing: `_dry_run_pack` returned no warnings at all, and a diff row
read "language_evidence: 2 → 3".

What this suite pins: an override naming a signal the model does not have is
refused at PROPOSE (not discovered at screening time, where it is a requisition
nobody can screen); a pack RAISING a cap above the model's is allowed and
WARNED, because that is the one direction a reader would not assume; and the same
check runs again at publish, because a proposal validated last week can be
approved after a model change.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import change_requests as cr
from resume_screener import governance
from resume_screener.policy import PolicyError, load_pack, resolve_policy
from resume_screener.registry import apply_policy, load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNALS = REPO_ROOT / "signals"


@pytest.fixture()
def packs(tmp_path):
    """A private copy of policies/, so a test never edits the committed packs."""
    shutil.copytree(REPO_ROOT / "policies", tmp_path / "policies")
    return tmp_path / "policies"


def dry_run(packs, jurisdiction: str, overrides: dict):
    return cr._dry_run_pack(jurisdiction, {"signal_overrides": overrides},
                            packs, SIGNALS)


def model_cap(signal_id: str) -> float:
    return next(s.cap_points for s in load_registry(SIGNALS).signals if s.id == signal_id)


# --- a raised cap is allowed, and said out loud ------------------------------------


def test_raising_a_cap_above_the_models_is_warned_never_blocked(packs):
    cap = model_cap("skill_recency") + 2
    summary, warnings, _version, diff = dry_run(
        packs, "SG", {"skill_recency": {"cap_points": cap}})

    assert warnings, "an approver should be told this out-steps the model"
    note = next(w for w in warnings if "skill_recency" in w["path"])
    assert note["level"] == "amber"
    assert f"±{cap:g}" in note["text"] and "company-wide model caps it" in note["text"]
    # and it is a proposal, not a refusal
    assert summary and diff


def test_the_diff_names_the_signal_and_prices_it_against_the_model(packs):
    cap = model_cap("skill_recency") + 2
    _s, _w, _v, diff = dry_run(packs, "SG", {"skill_recency": {"cap_points": cap}})
    row = next(r for r in diff if "skill_recency" in r["path"])
    # "Skill recency", not "skill_recency"; and where the old number came from
    assert "Skill recency" in row["label"]
    assert "the model's" in row["from"]


def test_a_cap_of_zero_reads_as_off_here(packs):
    _s, warnings, _v, diff = dry_run(packs, "SG", {"skill_recency": {"cap_points": 0}})
    assert any("will not fire" in w["text"] for w in warnings)
    row = next(r for r in diff if "skill_recency" in r["path"])
    assert row["to"] == "off here"


def test_turning_on_a_signal_the_model_ships_off_is_warned(packs):
    _s, warnings, _v, _d = dry_run(
        packs, "SG", {"public_work_relevance": {"enabled": True}})
    note = next(w for w in warnings if "public_work_relevance" in w["path"])
    assert note["level"] == "amber"
    assert "OFF in the company-wide model" in note["text"]


def test_turning_one_off_is_stated_but_unremarkable(packs):
    _s, warnings, _v, _d = dry_run(packs, "SG", {"skill_recency": {"enabled": False}})
    note = next(w for w in warnings if "skill_recency" in w["path"])
    assert note["level"] == "info"


# --- a signal the model does not have ------------------------------------------------


def test_an_override_naming_an_unknown_signal_is_refused_at_propose(packs):
    """Not discovered at screening time, where it is a requisition nobody can
    screen and nothing on the approval explains why."""
    with pytest.raises(cr.ChangeRequestError) as exc:
        dry_run(packs, "SG", {"no_such_signal": {"enabled": True}})
    assert "no_such_signal" in str(exc.value)


def test_publishing_one_is_refused_too(packs):
    """The same check at PUBLISH, because a proposal validated last week can be
    approved after a model publish that retired the signal it names."""
    from resume_screener.registry import RegistryError

    with pytest.raises((RegistryError, PolicyError)):
        governance.publish_pack(
            "SG", signal_overrides={"no_such_signal": {"enabled": True}},
            author="sa@example.com", rationale="test",
            policies_dir=packs, log_path=packs / "audit.jsonl")


# --- what a publish actually does ------------------------------------------------------


def test_an_approved_dial_change_reaches_what_a_run_would_use(packs):
    cap = model_cap("skill_recency") + 2
    version = governance.publish_pack(
        "SG", signal_overrides={"skill_recency": {"cap_points": cap}},
        author="sa@example.com", rationale="market calibration",
        policies_dir=packs, log_path=packs / "audit.jsonl")
    assert version == load_pack(packs / "SG.yaml").version

    enabled, _excluded = apply_policy(
        load_registry(SIGNALS), resolve_policy("SG", packs))
    live = next(s for s in enabled if s.id == "skill_recency")
    assert live.cap_points == cap

    # and only SG moved
    other = apply_policy(load_registry(SIGNALS), resolve_policy("JP", packs))[0]
    assert next(s for s in other if s.id == "skill_recency").cap_points == model_cap("skill_recency")


def test_a_pack_still_cannot_touch_anything_structural(packs):
    """The model owns the total adjustment cap, and a pack has no field for it —
    so an attempt is a validation error, not a quiet success."""
    from resume_screener.models import PolicyPack

    with pytest.raises(Exception):
        PolicyPack.model_validate({"jurisdiction": "SG", "adjustments": {"total_cap_points": 20}})
