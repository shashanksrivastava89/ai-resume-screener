"""Feedback tests (P12): append/load round-trip, the required-reason guarantee,
newest-decision-wins, corrupt-line tolerance, versions flattening. Offline."""

import pytest

from resume_screener.feedback import (
    latest_decision_for,
    load_decisions,
    record_decision,
    versions_from_run_meta,
)
from resume_screener.models import Decision


def _decision(candidate="cid1", reason="Good treasury depth", action="agree", ts="2026-07-06T10:00:00+00:00") -> Decision:
    return Decision(
        candidate_id=candidate, run_id="run-1", suggestion="Strong (94.5)",
        human_action=action, final_band=None, reason=reason, reviewer="Shashank",
        versions={"rubric": "role-1.rubric.v1.yaml (v1)", "policy_pack": "IN"}, ts=ts,
    )


def test_record_and_load_roundtrip(tmp_path):
    path = record_decision(_decision(), tmp_path)
    assert path.name == "decisions.jsonl"
    loaded = load_decisions(tmp_path)
    assert len(loaded) == 1
    assert loaded[0] == _decision()


def test_reason_is_required_in_code_not_just_ui(tmp_path):
    with pytest.raises(ValueError, match="reason"):
        record_decision(_decision(reason=""), tmp_path)
    with pytest.raises(ValueError, match="reason"):
        record_decision(_decision(reason="   "), tmp_path)
    assert load_decisions(tmp_path) == []  # nothing was written


def test_append_only_newest_decision_wins(tmp_path):
    record_decision(_decision(reason="first look", action="agree"), tmp_path)
    record_decision(_decision(reason="changed my mind", action="override",
                              ts="2026-07-06T11:00:00+00:00"), tmp_path)
    decisions = load_decisions(tmp_path)
    assert len(decisions) == 2  # append-only: history preserved
    latest = latest_decision_for(decisions, "run-1", "cid1")
    assert latest.human_action == "override" and latest.reason == "changed my mind"


def test_corrupt_line_skipped_not_fatal(tmp_path):
    record_decision(_decision(), tmp_path)
    with (tmp_path / "decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{this is not json\n")
    record_decision(_decision(candidate="cid2"), tmp_path)
    loaded = load_decisions(tmp_path)
    assert [d.candidate_id for d in loaded] == ["cid1", "cid2"]


def test_versions_from_run_meta_flattens_everything():
    meta = {
        "jurisdiction": "IN",
        "policy_pack": "IN",
        "versions": {
            "app": "resume-screener 0.1.0",
            "chat_deployment": "gpt-4.1",
            "prompts": {"profile": "p5.1", "layer2": "p9.1"},
            "policy_files": {"base": "abc123", "IN": "def456"},
            "rubric": "role-1.rubric.v1.yaml (v1)",
        },
    }
    flat = versions_from_run_meta(meta)
    assert flat["chat_deployment"] == "gpt-4.1"
    assert flat["prompts.profile"] == "p5.1"
    assert flat["policy_files.IN"] == "def456"
    assert flat["jurisdiction"] == "IN" and flat["policy_pack"] == "IN"
    assert all(isinstance(v, str) for v in flat.values())  # Decision.versions is str->str
