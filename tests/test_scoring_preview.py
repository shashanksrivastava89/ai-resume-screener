"""P51 live calculator: draft model values + a synthetic candidate through the
REAL pure scoring functions — caps clamp, the total cap scales, suspensions drop
points, gates flip the label, draft bands re-band, limits 422, and nothing is
ever written to disk."""

from __future__ import annotations

import pytest

from resume_screener.api import score_math
from resume_screener.scoring_model import default_scoring_model
from tests.test_staleness import _as, client, workspace  # noqa: F401


def _model(**overrides):
    m = default_scoring_model()
    for path, value in overrides.items():
        section, _, field = path.partition(".")
        setattr(getattr(m, section), field, value)
    return m


def _candidate(**overrides):
    spec = {
        "requirements": [
            {"label": "GL accounting", "kind": "must_have", "weight": 3, "match": "full"},
            {"label": "Tally", "kind": "nice_to_have", "weight": 1, "match": "partial"},
        ],
        "adjustments": [],
        "preferences": [],
        "extraction_confidence": 0.95,
        "downgraded_findings": 0,
    }
    spec.update(overrides)
    return spec


def test_base_math_and_banding():
    out = score_math.run_preview(_model(), _candidate())
    # weights 3:1 → 0.75·1.0 + 0.25·0.5 = 0.875 → base 87.5 → Possible (< 88)
    assert out["base"]["total"] == 87.5
    assert out["result"]["band_label"] == "Possible match"
    assert out["waterfall"][0]["step"] == "base"
    assert out["waterfall"][-1]["step"] == "band"


def test_draft_band_values_rebrand_live():
    out = score_math.run_preview(_model(**{"bands.strong_at": 85.0}), _candidate())
    assert out["result"]["band"] == "Strong"


def test_per_factor_cap_clamps_with_a_note():
    out = score_math.run_preview(_model(), _candidate(adjustments=[
        {"label": "Regulated exposure", "points": 9, "cap_points": 3,
         "direction": "plus", "verdict": "pass"}]))
    step = next(w for w in out["waterfall"] if w["step"] == "adjustment")
    assert step["delta"] == 3.0
    assert "reduced from +9 to +3" in step["note"]


def test_total_cap_scales_all_extras():
    out = score_math.run_preview(_model(), _candidate(adjustments=[
        {"label": "A", "points": 6, "cap_points": 6, "direction": "plus", "verdict": "pass"},
        {"label": "B", "points": 6, "cap_points": 6, "direction": "plus", "verdict": "pass"}]))
    net = sum(w["delta"] for w in out["waterfall"] if w["step"] == "adjustment")
    assert net == 8.0  # DEFAULT total cap
    assert out["enforcement_notes"]


def test_suspended_extra_contributes_zero():
    out = score_math.run_preview(_model(), _candidate(adjustments=[
        {"label": "Tenure pattern", "points": 5, "cap_points": 6,
         "direction": "plus", "verdict": "suspend"}]))
    step = next(w for w in out["waterfall"] if w["step"] == "suspended")
    assert step["delta"] == 0.0 and "fairness check" in step["note"]
    assert out["result"]["final_score"] == 87.5  # base only


def test_failed_gate_reads_gate_not_met():
    out = score_math.run_preview(_model(), _candidate(requirements=[
        {"label": "CA certification", "kind": "hard_gate", "weight": 0, "match": "partial"},
        {"label": "GL accounting", "kind": "must_have", "weight": 1, "match": "full"}]))
    assert out["result"]["eligible"] is False
    assert out["result"]["band_label"] == "Gate not met"


def test_preference_clamps_to_draft_cap():
    out = score_math.run_preview(_model(**{"preferences.per_preference_cap_points": 3.0}),
                                 _candidate(preferences=[{"label": "Tally shops", "points": 9}]))
    step = next(w for w in out["waterfall"] if w["step"] == "preference")
    assert step["delta"] == 3.0 and "reduced from +9 to +3" in step["note"]


def test_input_limits_and_validation():
    with pytest.raises(score_math.PreviewError, match="at least one requirement"):
        score_math.run_preview(_model(), _candidate(requirements=[]))
    with pytest.raises(score_math.PreviewError, match="At most"):
        score_math.run_preview(_model(), _candidate(
            adjustments=[{"label": "x", "points": 1}] * 21))
    with pytest.raises(score_math.PreviewError, match="Unknown match level"):
        score_math.run_preview(_model(), _candidate(requirements=[
            {"label": "x", "kind": "must_have", "weight": 1, "match": "maybe"}]))


# --- the API route: draft values honored, nothing written --------------------------


def _snapshot(root):
    return sorted(str(p) for p in root.rglob("*") if p.is_file())


def test_preview_route_accepts_drafts_and_writes_nothing(workspace, client):
    before = _snapshot(workspace)
    r = client.post("/api/scoring/preview", headers=_as("HRHead"), json={
        "values": {"bands.strong_at": 80},
        "candidate": _candidate()})
    assert r.status_code == 200
    assert r.json()["result"]["band"] == "Strong"  # 87.5 ≥ draft 80
    assert _snapshot(workspace) == before  # zero persistence
    # invalid draft → 422 with a plain message
    bad = client.post("/api/scoring/preview", headers=_as("HRHead"), json={
        "values": {"bands.strong_at": 30}, "candidate": _candidate()})
    assert bad.status_code == 422
    # TASpecialist has no rules_view → 403
    assert client.post("/api/scoring/preview", headers=_as("TASpecialist"),
                       json={"candidate": _candidate()}).status_code == 403
