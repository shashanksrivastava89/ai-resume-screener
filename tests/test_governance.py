"""Governance rails on the merged scoring model: editable rules must never break
auditability.

Every publish writes a NEW immutable scoring_model.v{N}.yaml + an audit entry;
removing a floor topic requires the explicit exposure acknowledgment; a new
signal needs its job-relatedness rationale (schema) and starts OFF; retiring a
rule removes it from scoring but keeps it in the file."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import governance
from resume_screener.policy import resolve_policy
from resume_screener.registry import apply_policy, load_registry
from resume_screener.scoring_model import (
    ScoringModelError,
    canonical_dump,
    load_scoring_model,
)

SEED = Path("signals/scoring_model.v1.yaml")


def _signals_dir(tmp_path: Path) -> Path:
    d = tmp_path / "signals"
    d.mkdir()
    shutil.copy(SEED, d / "scoring_model.v1.yaml")
    return d


def _draft(dir: Path, mutate) -> dict:
    data = canonical_dump(load_scoring_model(dir))
    mutate(data)
    return data


def _enabled_ids(dir: Path) -> set[str]:
    reg = load_registry(dir)
    return {s.id for s in apply_policy(reg, resolve_policy("IN"))[0]}


# --- editing a rule bumps the version and writes an audit entry ---------------------------


def test_edit_cap_bumps_version_and_audits(tmp_path):
    d = _signals_dir(tmp_path)
    log = tmp_path / "audit.jsonl"

    def bump(data):
        next(s for s in data["signals"] if s["id"] == "skill_recency")["cap_points"] = 4.0

    version = governance.publish_scoring_model(
        _draft(d, bump), author="tester",
        rationale="calibration: recency deserves more room", dir=d, log_path=log)

    assert version == 2
    assert (d / "scoring_model.v2.yaml").exists()
    assert (d / "scoring_model.v1.yaml").exists()  # old versions are never touched
    sm = load_scoring_model(d)
    assert sm.version == 2
    assert sm.signal_by_id("skill_recency").cap_points == 4.0
    audit = governance.load_audit(log)
    assert audit[-1]["action"] == "publish_scoring_model"
    assert audit[-1]["version"] == 2
    assert audit[-1]["author"] == "tester"
    assert "calibration" in audit[-1]["rationale"]
    assert "Skill recency" in audit[-1]["summary"]


def test_publish_requires_a_rationale(tmp_path):
    d = _signals_dir(tmp_path)
    with pytest.raises(ScoringModelError, match="reason for the change is required"):
        governance.publish_scoring_model(
            _draft(d, lambda data: None), author="t", rationale="  ",
            dir=d, log_path=tmp_path / "a.jsonl")


def test_noop_draft_is_rejected(tmp_path):
    d = _signals_dir(tmp_path)
    with pytest.raises(ScoringModelError, match="Nothing changed"):
        governance.publish_scoring_model(
            _draft(d, lambda data: None), author="t", rationale="tweak",
            dir=d, log_path=tmp_path / "a.jsonl")


# --- creating a rule: schema requires job-relatedness; new rules start OFF -----------------


def test_create_rule_requires_job_relatedness(tmp_path):
    d = _signals_dir(tmp_path)

    def add(data):
        data["signals"].append({"id": "side_projects", "description": "d",
                                "direction": "plus", "kind": "judged",
                                "cap_points": 2.0, "enabled_default": False})

    with pytest.raises(ScoringModelError, match="job_relatedness"):
        governance.publish_scoring_model(_draft(d, add), author="t", rationale="add it",
                                         dir=d, log_path=tmp_path / "a.jsonl")


def test_new_rule_must_start_off(tmp_path):
    d = _signals_dir(tmp_path)

    def add(data):
        data["signals"].append({"id": "portfolio_depth", "description": "d",
                                "direction": "plus", "kind": "judged", "cap_points": 2.0,
                                "enabled_default": True,
                                "job_relatedness": "Delivered work predicts ramp."})

    with pytest.raises(ScoringModelError, match="start switched OFF"):
        governance.publish_scoring_model(_draft(d, add), author="t", rationale="add it",
                                         dir=d, log_path=tmp_path / "a.jsonl")


def test_create_rule_lands_off_then_a_second_change_enables_it(tmp_path):
    d = _signals_dir(tmp_path)
    log = tmp_path / "a.jsonl"
    spec = {"id": "portfolio_depth", "display_name": "Portfolio depth",
            "description": "Depth of relevant delivered work.", "definition": "…",
            "direction": "plus", "kind": "judged", "cap_points": 2.0,
            "enabled_default": False,
            "job_relatedness": "Delivered work predicts ramp."}
    governance.publish_scoring_model(
        _draft(d, lambda data: data["signals"].append(dict(spec))),
        author="t", rationale="pilot a portfolio signal", dir=d, log_path=log)
    assert "portfolio_depth" not in _enabled_ids(d)  # off until switched on

    def enable(data):
        next(s for s in data["signals"] if s["id"] == "portfolio_depth")["enabled_default"] = True

    governance.publish_scoring_model(_draft(d, enable), author="t",
                                     rationale="switch it on", dir=d, log_path=log)
    assert "portfolio_depth" in _enabled_ids(d)


def test_retire_removes_from_scoring_but_keeps_in_file(tmp_path):
    d = _signals_dir(tmp_path)
    assert "skill_recency" in _enabled_ids(d)

    def retire(data):
        next(s for s in data["signals"] if s["id"] == "skill_recency")["retired"] = True

    governance.publish_scoring_model(_draft(d, retire), author="t",
                                     rationale="retire recency for now",
                                     dir=d, log_path=tmp_path / "a.jsonl")
    assert "skill_recency" not in _enabled_ids(d)  # never applied
    assert load_scoring_model(d).signal_by_id("skill_recency").retired  # kept


# --- the floor rail: removing a floor topic needs the exposure acknowledgment --------------


def test_floor_topic_removal_needs_acknowledgment(tmp_path):
    d = _signals_dir(tmp_path)

    def remove(data):
        data["topics"] = [t for t in data["topics"] if t["id"] != "salary_history"]

    with pytest.raises(ScoringModelError, match="policy-floor protection"):
        governance.publish_scoring_model(_draft(d, remove), author="t",
                                         rationale="drop salary history",
                                         dir=d, log_path=tmp_path / "a.jsonl")

    version = governance.publish_scoring_model(
        _draft(d, remove), author="t", rationale="drop salary history",
        acknowledged_floor_removals=["salary_history"],
        dir=d, log_path=tmp_path / "a.jsonl")
    assert version == 2
    assert load_scoring_model(d).topic_by_id("salary_history") is None
    # ...and the registry view (what the critic enforces) follows
    assert "salary_history" not in {e.id for e in load_registry(d).excluded}


def test_adding_a_topic_is_allowed(tmp_path):
    d = _signals_dir(tmp_path)

    def add(data):
        data["topics"].append({"id": "social_media_activity", "name": "Social media activity",
                               "reason": "off-the-job conduct proxy",
                               "stages": ["scoring", "preferences"],
                               "phrases": ["social media"]})

    governance.publish_scoring_model(_draft(d, add), author="t",
                                     rationale="add social-media topic",
                                     dir=d, log_path=tmp_path / "a.jsonl")
    assert "social_media_activity" in {e.id for e in load_registry(d).excluded}


# --- review sign-off drives the pending banner --------------------------------------------


def test_review_pending_clears_on_signoff_then_reopens_on_change(tmp_path):
    d = _signals_dir(tmp_path)
    log, status = tmp_path / "audit.jsonl", tmp_path / "review.json"
    assert governance.review_pending(status, log)  # no review yet
    governance.record_review("HR Head; Counsel", "2026-07-07", "tester", status, log)
    assert not governance.review_pending(status, log)

    def bump(data):
        next(s for s in data["signals"] if s["id"] == "skill_recency")["cap_points"] = 3.5

    governance.publish_scoring_model(_draft(d, bump), author="t", rationale="tweak",
                                     dir=d, log_path=log)
    assert governance.review_pending(status, log)  # a later direct change reopens it
