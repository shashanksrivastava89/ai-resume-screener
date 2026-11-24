"""P51 golden pin: wiring the engine to the scoring model must not change a
single byte of any ScoreResult when the model's values equal the old defaults.

Every case runs assemble_score twice — once with the pre-P51 keyword defaults,
once with every value injected from the committed seed (the way cli.py now calls
it) — and asserts model_dump_json() identity. Same for score_layer1."""

from __future__ import annotations

from pathlib import Path

import yaml

from resume_screener.findings import FindingsRecord
from resume_screener.models import (
    Adjustment,
    CriticReview,
    Finding,
    Layer1Result,
    Layer2Result,
    PreferenceAdjustment,
    Requirement,
    Rubric,
)
from resume_screener.layer1 import score_layer1
from resume_screener.scoring import assemble_score
from resume_screener.scoring_model import ScoringModel

SEED = Path(__file__).resolve().parents[1] / "signals" / "scoring_model.v1.yaml"
SM = ScoringModel.model_validate(yaml.safe_load(SEED.read_text(encoding="utf-8")))


def _l1(base=80.0, eligible=True, missing=(), reasons=()) -> Layer1Result:
    return Layer1Result(
        candidate_id="cid", rubric_version=1,
        gate_flags=[] if eligible else ["r03"],
        must_haves_missing=list(missing),
        coverage_score=base / 100, base_score=base,
        eligible=eligible, reasons=list(reasons),
    )


def _l2(*points_by_signal: tuple[str, float]) -> Layer2Result:
    return Layer2Result(
        candidate_id="cid",
        adjustments=[Adjustment(signal_id=s, points=p, evidence="e", rationale="r")
                     for s, p in points_by_signal],
        narrative="n",
    )


def _pref(pid="p1", pts=2.0):
    return PreferenceAdjustment(pref_id=pid, owner="ta@x", text="t", points=pts,
                                evidence="e", rationale="r")


# NOTE: bands were recalibrated in P13 (88/60) and the seed carries THAT truth,
# while assemble_score's keyword defaults stayed at the original 75/50. The pin
# is therefore "defaults + explicit 88/60/Possible" == "everything from the seed"
# — i.e. exactly what cli.py passed before vs after the P51 rewiring.
_OLD_CALL = dict(band_strong=88, band_possible=60, cap_band_if_must_have_missing="Possible")
_NEW_CALL = dict(
    band_strong=SM.bands.strong_at, band_possible=SM.bands.possible_at,
    cap_band_if_must_have_missing=SM.bands.cap_if_must_have_missing,
    preference_cap=SM.preferences.per_preference_cap_points,
    confidence_rules=SM.confidence,
)

CASES = [
    # (name, l1, l2, critic, prefs, extraction_confidence, downgraded)
    ("plain", _l1(base=80), None, None, None, 0.95, 0),
    ("gate_fail", _l1(base=90, eligible=False, reasons=["Hard requirement not met: CA"]),
     None, None, None, 0.95, 0),
    ("must_have_cap", _l1(base=92, missing=["r01"], reasons=["Must-have not evidenced: GAAP"]),
     None, None, None, 0.95, 0),
    ("suspended", _l1(base=70), _l2(("s1", 6.0), ("s2", 4.0)),
     CriticReview(verdict_per_adjustment={"s1": "suspend", "s2": "pass"}), None, 0.95, 0),
    ("pref_clamped", _l1(base=70), None, None, [_pref(pts=5.0), _pref("p2", -1.0)], 0.95, 0),
    ("unmeasured_docx", _l1(base=61), None, None, None, None, 1),
    ("low_confidence", _l1(base=88), None, None, None, 0.6, 3),
]


def test_scoreresults_byte_identical_defaults_vs_seed():
    for name, l1, l2, critic, prefs, conf, downgraded in CASES:
        common = dict(role_title="Role", jurisdiction="IN",
                      extraction_confidence=conf, downgraded_findings=downgraded,
                      preference_adjustments=prefs)
        old = assemble_score(l1, l2, critic, **common, **_OLD_CALL)
        new = assemble_score(l1, l2, critic, **common, **_NEW_CALL)
        assert old.model_dump_json() == new.model_dump_json(), f"case {name} drifted"


def test_layer1_byte_identical_defaults_vs_seed():
    rubric = Rubric(
        role_title="Role", jd_ref="jd.md", version=1,
        requirements=[
            Requirement(id="r01", text="GL accounting", type="skill", kind="must_have", weight=3),
            Requirement(id="r02", text="Tally", type="skill", kind="nice_to_have", weight=1),
            Requirement(id="r03", text="CA certification", type="certification", kind="hard_gate", weight=0),
        ],
    )
    record = FindingsRecord(
        candidate_id="cid", role_title="Role", rubric_version=1,
        findings=[
            Finding(requirement_id="r01", requirement_text="GL accounting", match="full", evidence="e"),
            Finding(requirement_id="r02", requirement_text="Tally", match="partial", evidence="e"),
            Finding(requirement_id="r03", requirement_text="CA certification", match="full", evidence="e"),
        ],
    )
    old = score_layer1(record, rubric)
    new = score_layer1(record, rubric, match_value=SM.coverage.match_value)
    assert old.model_dump_json() == new.model_dump_json()


# --- and the knobs genuinely steer when changed --------------------------------------


def test_preference_cap_knob_steers():
    l1 = _l1(base=70)
    common = dict(role_title="R", jurisdiction="IN", extraction_confidence=0.95,
                  downgraded_findings=0, preference_adjustments=[_pref(pts=5.0)])
    assert assemble_score(l1, None, None, **common).final_score == 72.0  # default ±2
    assert assemble_score(l1, None, None, **common, preference_cap=4.0).final_score == 74.0


def test_confidence_rules_knob_steers():
    strict = SM.confidence.model_copy(update={"high_requires_at_least": 0.99})
    result = assemble_score(
        _l1(base=80), None, None, role_title="R", jurisdiction="IN",
        extraction_confidence=0.95, downgraded_findings=0, confidence_rules=strict,
    )
    assert result.confidence == "medium"  # 0.95 no longer clears the bar


def test_match_value_knob_steers():
    rubric = Rubric(
        role_title="Role", jd_ref="jd.md", version=1,
        requirements=[Requirement(id="r01", text="t", type="skill", kind="nice_to_have", weight=1)],
    )
    record = FindingsRecord(
        candidate_id="cid", role_title="Role", rubric_version=1,
        findings=[Finding(requirement_id="r01", requirement_text="t", match="partial", evidence="e")],
    )
    assert score_layer1(record, rubric).base_score == 50.0
    tweaked = {"full": 1.0, "partial": 0.7, "none": 0.0}
    assert score_layer1(record, rubric, match_value=tweaked).base_score == 70.0
