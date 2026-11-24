"""Scoring tests (P11): band math, must-have cap, gate rationale, clamping,
the suspension-recomputes-the-score guarantee (definition of done), confidence."""

from resume_screener.models import (
    Adjustment,
    CriticReview,
    Layer1Result,
    Layer2Result,
    PreferenceAdjustment,
)
from resume_screener.scoring import assemble_score, confidence_rating


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


def _score(l1, l2=None, critic=None, **kw):
    defaults = dict(role_title="Role", jurisdiction="IN",
                    extraction_confidence=0.95, downgraded_findings=0)
    defaults.update(kw)
    return assemble_score(l1, l2, critic, **defaults)


# --- bands & thresholds -----------------------------------------------------------


def test_band_thresholds_from_config():
    assert _score(_l1(base=75)).fit_band == "Strong"  # >= is Strong
    assert _score(_l1(base=74.9)).fit_band == "Possible"
    assert _score(_l1(base=50)).fit_band == "Possible"
    assert _score(_l1(base=49.9)).fit_band == "Not a fit"
    # custom thresholds flow through
    assert _score(_l1(base=60), band_strong=60).fit_band == "Strong"


def test_adjustments_move_the_band():
    result = _score(_l1(base=70), _l2(("s1", 6.0)),
                    CriticReview(verdict_per_adjustment={"s1": "pass"}))
    assert result.final_score == 76.0 and result.fit_band == "Strong"


def test_clamped_at_100_and_0():
    high = _score(_l1(base=95), _l2(("s1", 12.0)),
                  CriticReview(verdict_per_adjustment={"s1": "pass"}))
    assert high.final_score == 100.0
    low = _score(_l1(base=3), _l2(("s1", -8.0)),
                 CriticReview(verdict_per_adjustment={"s1": "pass"}))
    assert low.final_score == 0.0


# --- the definition-of-done case: suspension recomputes the score -------------------


def test_suspension_removes_points_from_final():
    l2 = _l2(("s1", 6.0), ("s2", 4.0))
    all_pass = CriticReview(verdict_per_adjustment={"s1": "pass", "s2": "pass"})
    one_suspended = CriticReview(verdict_per_adjustment={"s1": "suspend", "s2": "pass"})

    assert _score(_l1(base=70), l2, all_pass).final_score == 80.0
    recomputed = _score(_l1(base=70), l2, one_suspended)
    assert recomputed.final_score == 74.0  # s1's +6 is gone...
    assert recomputed.layer2 is not None and len(recomputed.layer2.adjustments) == 2  # ...record intact
    assert "suspended" in recomputed.summary_rationale
    assert recomputed.fit_band == "Possible"  # and the band moved with it


def test_flag_keeps_points_but_mentions_it():
    review = CriticReview(verdict_per_adjustment={"s1": "flag"})
    result = _score(_l1(base=70), _l2(("s1", 5.0)), review)
    assert result.final_score == 75.0
    assert "flagged" in result.summary_rationale


# --- gate + must-have cap -------------------------------------------------------------


def test_ineligible_is_not_a_fit_gate_with_reasons():
    l1 = _l1(base=90, eligible=False, reasons=["Hard requirement not met: CA required"])
    result = _score(l1)
    assert result.fit_band == "Not a fit"
    assert result.summary_rationale.startswith("Not a fit (gate):")
    assert "CA required" in result.summary_rationale
    assert result.final_score == 90.0  # still computed, still visible


def test_missing_must_have_caps_the_band():
    l1 = _l1(base=85, missing=["r01"], reasons=["Must-have not evidenced: US GAAP"])
    result = _score(l1)
    assert result.final_score == 85.0  # score untouched...
    assert result.fit_band == "Possible"  # ...but the band is capped
    assert "capped" in result.summary_rationale


def test_must_have_cap_does_not_lift_a_low_band():
    result = _score(_l1(base=40, missing=["r01"]))
    assert result.fit_band == "Not a fit"  # cap only pulls DOWN, never up


def test_low_score_missing_must_have_does_not_claim_a_cap():
    # The "Rozy" case: eligible (hard gates met) but missing a must-have with low
    # coverage, so the band is already 'Not a fit'. The cap changes nothing, so the
    # rationale must NOT contradict the band by claiming it was "capped at Possible".
    result = _score(_l1(base=40, missing=["r01"]))
    assert result.fit_band == "Not a fit"
    assert "capped" not in result.summary_rationale
    assert "core requirement isn't evidenced" in result.summary_rationale


# --- confidence heuristic ----------------------------------------------------------------


def test_confidence_rating_matrix():
    assert confidence_rating(0.95, 0) == "high"
    assert confidence_rating(None, 0) == "medium"  # docx fallback: unmeasured
    assert confidence_rating(0.7, 0) == "low"  # scanned-looking resume
    assert confidence_rating(0.95, 3) == "low"  # too many downgraded findings
    assert confidence_rating(0.85, 1) == "medium"


# --- P40: HR-preference nudges (bounded ±2, folded into the ONE final clamp) --------


def _pref(pid="p1", pts=2.0, owner="ta@x", text="t"):
    return PreferenceAdjustment(pref_id=pid, owner=owner, text=text, points=pts,
                                evidence="e", rationale="r")


def test_no_prefs_scoreresult_is_byte_identical_golden():
    """The scoring-behavior-unchanged pin: with no applicable preferences the
    ScoreResult is model_dump-identical whether the arg is omitted, None or []."""
    l1 = _l1(base=80)
    omitted = _score(l1)                             # arg not passed -> None
    explicit_none = _score(l1, preference_adjustments=None)
    explicit_empty = _score(l1, preference_adjustments=[])
    assert omitted.model_dump() == explicit_none.model_dump() == explicit_empty.model_dump()
    d = omitted.model_dump()
    # the new field is present, empty, and inert — nothing else shifted
    assert d["preference_adjustments"] == []
    assert d["final_score"] == 80.0 and d["fit_band"] == "Strong"
    assert d["summary_rationale"] == "Covers 80% of weighted requirements (base 80)."
    assert "hiring-preference" not in d["summary_rationale"]  # no pref sentence leaks


def test_preferences_nudge_the_final_and_can_move_the_band():
    result = _score(_l1(base=74), preference_adjustments=[_pref(pts=2.0)])
    assert result.final_score == 76.0 and result.fit_band == "Strong"
    assert "1 hiring-preference note(s), net +2.0." in result.summary_rationale


def test_per_preference_clamp_at_two():
    # a +5 preference is clamped to +2 before it reaches the score
    assert _score(_l1(base=70), preference_adjustments=[_pref(pts=5.0)]).final_score == 72.0


def test_a_preference_never_subtracts_even_here():
    """Add-only since 2026-09-25. pref_match already drops a negative, and this is the
    last word — it used to clamp to ±2, so a −5 took the score to 68."""
    assert _score(_l1(base=70), preference_adjustments=[_pref(pts=-5.0)]).final_score == 70.0


def test_multiple_prefs_sum_then_ONE_final_clamp():
    prefs = [_pref("p1", 2.0), _pref("p2", 2.0), _pref("p3", 2.0)]  # +6 raw
    # base 95 + 6 = 101 -> a SINGLE clamp to 100 (not per-pref)
    assert _score(_l1(base=95), preference_adjustments=prefs).final_score == 100.0


def test_prefs_and_layer2_fold_into_one_clamp():
    l2 = _l2(("s1", 6.0))
    critic = CriticReview(verdict_per_adjustment={"s1": "pass"})
    result = _score(_l1(base=70), l2, critic, preference_adjustments=[_pref(pts=2.0)])
    assert result.final_score == 78.0  # 70 + 6 (L2) + 2 (pref)


def test_ineligible_gets_pref_net_but_keeps_gate_band():
    l1 = _l1(base=90, eligible=False, reasons=["Hard requirement not met: CA required"])
    result = _score(l1, preference_adjustments=[_pref(pts=2.0)])
    assert result.fit_band == "Not a fit"  # a preference NEVER lifts a gate failure
    assert result.summary_rationale.startswith("Not a fit (gate):")
    assert result.final_score == 92.0  # ...but the nudge still shows in the number


# --- shortlist order (2026-09-16) ------------------------------------------------
#
# Reported from UAT req 2026-39185: two candidates who passed every hard gate were
# listed BELOW candidates who had failed one. The ranking started at the band, and a
# failed gate only forces the band to "Not a fit" — which a candidate scoring under
# the Possible cutoff is already in. Inside that bottom band the tie-break was the raw
# score, so a gate-failer on 71 outranked a gate-passer on 54, while the Review filter
# and the drawer both told reviewers "missing one puts this candidate below everyone
# who meets them all".


def _ranked(*rows):
    """rows = (candidate_id, band, score, eligible) — highest first."""
    from resume_screener.scoring import rank_key

    return [r[0] for r in sorted(
        rows, key=lambda r: rank_key(r[1], r[2], r[0], eligible=r[3]), reverse=True)]


def test_a_gate_passer_is_never_below_a_gate_failer():
    """Whatever the scores say. A hard gate is pass/fail by definition."""
    assert _ranked(
        ("failer", "Not a fit", 71.0, False),
        ("passer", "Not a fit", 54.0, True),
    ) == ["passer", "failer"]


def test_the_band_still_leads_among_candidates_who_all_passed():
    assert _ranked(
        ("weak", "Not a fit", 59.0, True),
        ("possible", "Possible", 60.0, True),
        ("strong", "Strong", 88.0, True),
    ) == ["strong", "possible", "weak"]


def test_gate_failers_keep_their_own_order_by_score():
    """Everyone stays listed with their reasons (§6.3) — they are ordered, not hidden."""
    assert _ranked(
        ("low", "Not a fit", 30.0, False),
        ("high", "Not a fit", 80.0, False),
    ) == ["high", "low"]


def test_the_reports_and_the_review_list_order_identically():
    """Two copies of an ordering rule is how an exported shortlist comes to disagree
    with the screen it was exported from, so both call `scoring.rank_key`."""
    import inspect

    from resume_screener.api import review as review_mod
    from resume_screener import report as report_mod

    assert "rank_key" in inspect.getsource(review_mod._rank_key)
    assert "rank_key" in inspect.getsource(report_mod.rank_bundles)
