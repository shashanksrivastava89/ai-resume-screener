"""Layer-1 tests (P8, gate contract reshaped in P33): weight normalization,
match math, gate reasons, and the REPLAY guarantee — same frozen artifact +
same rubric version = identical result. All offline; Layer 1 is pure code."""

import json

import pytest

from resume_screener.findings import FindingsRecord
from resume_screener.layer1 import Layer1Error, passes_gate, score_layer1
from resume_screener.models import Finding, Requirement, Rubric

# The real wording behind each fixture id — findings are keyed to CONTENT
# (§layer1.Layer1Error docstring), so every FindingsRecord in this file carries
# this same text, exactly like a real artifact would.
_TEXT = {"r01": "US GAAP", "r02": "Oracle EBS", "r03": "CA qualification stated"}


def _rubric(weights=(3, 1), gate=False, version=1) -> Rubric:
    """Two weighted requirements (must + nice) with RAW weights (normalization
    is the scorer's job), optionally one hard-gate row in the SAME list."""
    requirements = [
        Requirement(id="r01", text=_TEXT["r01"], kind="must_have", weight=weights[0]),
        Requirement(id="r02", text=_TEXT["r02"], kind="nice_to_have", weight=weights[1]),
    ]
    if gate:
        requirements.append(
            Requirement(id="r03", text=_TEXT["r03"], kind="hard_gate", weight=0)
        )
    return Rubric(role_title="Role", jd_ref="jd.md", version=version, requirements=requirements)


def _record(matches: dict[str, str], version=1, text: dict[str, str] | None = None) -> FindingsRecord:
    text = text or _TEXT
    return FindingsRecord(
        candidate_id="cid1", role_title="Role", rubric_version=version,
        findings=[
            Finding(requirement_id=rid, requirement_text=text[rid], match=match,
                    evidence="q" if match != "none" else None)
            for rid, match in matches.items()
        ],
    )


# --- coverage math -------------------------------------------------------------------


def test_weight_normalization_3_to_1():
    # raw weights 3:1 normalize to 0.75/0.25; full on the 3, none on the 1.
    result = score_layer1(_record({"r01": "full", "r02": "none"}), _rubric())
    assert result.coverage_score == 0.75
    assert result.base_score == 75.0


def test_match_values_full_partial_none():
    result = score_layer1(_record({"r01": "partial", "r02": "full"}), _rubric())
    # 0.75*0.5 + 0.25*1.0 = 0.625
    assert result.coverage_score == 0.625
    assert result.base_score == 62.5
    zero = score_layer1(_record({"r01": "none", "r02": "none"}), _rubric())
    assert zero.base_score == 0.0
    full = score_layer1(_record({"r01": "full", "r02": "full"}), _rubric())
    assert full.base_score == 100.0


def test_equal_weights_when_yaml_has_zeros():
    # An all-zero-weights rubric (bad human edit) scores as uniform, not crash.
    result = score_layer1(_record({"r01": "full", "r02": "none"}), _rubric(weights=(0, 0)))
    assert result.coverage_score == 0.5


def test_gate_contributes_zero_to_coverage():
    # A passed gate must not move the base score: 3:1 weighted rows still
    # normalize to 0.75/0.25 with the gate in the list.
    record = _record({"r01": "full", "r02": "none", "r03": "full"})
    result = score_layer1(record, _rubric(gate=True))
    assert result.coverage_score == 0.75
    assert result.base_score == 75.0
    assert result.eligible is True
    assert passes_gate(result) is True


# --- gate + reasons ---------------------------------------------------------------------


def test_gate_failure_flags_but_keeps_candidate_listed():
    rubric = _rubric(gate=True)
    result = score_layer1(
        _record({"r01": "full", "r02": "full", "r03": "partial"}), rubric
    )
    assert result.eligible is False  # partial does NOT satisfy a hard gate
    assert result.gate_flags == ["r03"]
    assert "Hard requirement not met: CA qualification stated" in result.reasons
    assert result.base_score == 100.0  # still scored, still listed — never hidden
    assert passes_gate(result) is False


def test_missing_must_have_blocks_gate_with_reason():
    result = score_layer1(_record({"r01": "none", "r02": "full"}), _rubric())
    assert result.eligible is True  # no hard gates -> eligible
    assert result.must_haves_missing == ["r01"]
    assert any("Must-have not evidenced: US GAAP" in r for r in result.reasons)
    assert passes_gate(result) is False


def test_partial_must_have_is_not_missing():
    result = score_layer1(_record({"r01": "partial", "r02": "none"}), _rubric())
    assert result.must_haves_missing == []
    assert passes_gate(result) is True


# --- integrity guards ---------------------------------------------------------------------


def test_a_different_rubric_VERSION_NUMBER_alone_does_not_refuse_to_score():
    """2026-07-30 regression: findings are cached by CONTENT (résumé text + the
    exact requirement set), so the SAME candidate legitimately carries findings
    stamped with a DIFFERENT requisition's (or an earlier edit's) version number
    while the wording is unchanged — e.g. two requisitions drafted from the same
    JD, or a rubric edit that only reweights a row. That must score normally,
    not refuse. (Previously this raised Layer1Error on version mismatch alone —
    a live incident skipped 304 of 305 candidates on a real requisition because
    their correctly-cached findings carried a foreign req's version stamp.)"""
    record = _record({"r01": "full", "r02": "full"}, version=2)  # some OTHER req's stamp
    result = score_layer1(record, _rubric(version=1))  # THIS req is only at v1
    assert result.base_score == 100.0  # scored normally — no refusal


def test_changed_requirement_wording_still_refuses_to_score():
    """The real protection: if a requirement's actual WORDING changed since the
    finding was made (same id, different question — content genuinely drifted),
    scoring must still refuse rather than silently answer the wrong question."""
    record = _record({"r01": "full", "r02": "full"}, text={"r01": "US GAAP", "r02": "SAP FICO"})
    with pytest.raises(Layer1Error, match="different wording"):
        score_layer1(record, _rubric())  # rubric's r02 is "Oracle EBS", not "SAP FICO"


def test_incomplete_artifact_refuses_to_score():
    with pytest.raises(Layer1Error, match="no finding"):
        score_layer1(_record({"r01": "full"}), _rubric())  # r02 finding missing


def test_missing_gate_finding_refuses_to_score():
    # Gates live in the same list — an artifact without the gate's finding is
    # incomplete like any other missing requirement.
    with pytest.raises(Layer1Error, match="no finding"):
        score_layer1(_record({"r01": "full", "r02": "full"}), _rubric(gate=True))


# --- REPLAY: the audit guarantee ------------------------------------------------------------


def test_replay_from_serialized_artifact_is_identical():
    """Simulates the real flow: findings frozen to JSON on disk, reloaded later
    (e.g. during an audit), rescored — the Layer1Result must be IDENTICAL."""
    rubric = _rubric(gate=True)
    record = _record({"r01": "full", "r02": "partial", "r03": "full"})

    first = score_layer1(record, rubric)

    # Round-trip the artifact through JSON exactly like freeze_findings does.
    thawed = FindingsRecord.model_validate(json.loads(record.model_dump_json()))
    second = score_layer1(thawed, rubric)
    third = score_layer1(thawed, rubric)  # and once more, for determinism

    assert first == second == third
    assert first.model_dump_json() == second.model_dump_json()  # byte-identical


def test_replay_insensitive_to_finding_order():
    """The artifact's finding ORDER must not matter — scores key off ids."""
    rubric = _rubric(gate=True)
    record = _record({"r01": "full", "r02": "partial", "r03": "full"})
    shuffled = FindingsRecord(
        candidate_id=record.candidate_id, role_title=record.role_title,
        rubric_version=record.rubric_version, findings=list(reversed(record.findings)),
    )
    assert score_layer1(record, rubric) == score_layer1(shuffled, rubric)
