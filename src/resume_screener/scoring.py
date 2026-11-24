"""Final score assembly (P11, §5.6) — where the two layers and the critic meet.

The formula, in words
---------------------
final = clamp(Layer-1 base ± the adjustments that SURVIVED the critic, 0, 100)

Band rules (order matters):
1. Ineligible (failed a hard gate) -> band "Not a fit", rationale prefixed
   "Not a fit (gate):" with the reasons. Their score is still computed and they
   stay in every report — decision-support never hides anyone (§6.3).
2. Otherwise band by the scoring model's thresholds (signals/scoring_model.vN.yaml,
   P51): Strong >= bands.strong_at, Possible >= bands.possible_at, else "Not a fit".
3. Must-have cap: a candidate missing a must-have can band no higher than the
   model's cap_if_must_have_missing ("Possible" seeded) no matter the number —
   a great nice-to-have story cannot paper over a missing core requirement.

Confidence is a READ on the record, not on the candidate: how much should a
reviewer trust this row? It degrades with poor extraction (scanned/photographed
resumes) and with findings the evidence-verifier had to downgrade (the model
claimed things the text couldn't back).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from resume_screener.critic import apply_critic
from resume_screener.models import (
    Confidence,
    CriticReview,
    FitBand,
    Layer1Result,
    Layer2Result,
    PreferenceAdjustment,
    ScoreResult,
)

if TYPE_CHECKING:
    from resume_screener.scoring_model import ConfidenceRules

# HR preferences are bounded to 0..+2 points each (P40; add-only since 2026-09-25) —
# clamped here as a defense-in-depth even though pref_match already clamps. Since P51
# the live cap comes from the scoring model via `preference_cap`; this is the seeded
# default.
PREFERENCE_CAP = 2.0

# Band comparison helper: higher = better. Used for the must-have cap.
BAND_ORDER: dict[str, int] = {"Not a fit": 0, "Possible": 1, "Strong": 2}


def rank_key(fit_band: str, final_score: float, candidate_id: str,
             eligible: bool = True) -> tuple[int, int, float, str]:
    """The shortlist's order, defined ONCE (sort descending).

    ELIGIBILITY LEADS (2026-09-16). It used to start at the band, and a failed hard
    gate only forces the band down to "Not a fit" — which a candidate who simply
    scores under the Possible cutoff is already in. So inside that bottom band the
    tie-break was the raw score, and a gate-FAILER scoring 71 was listed above a
    gate-PASSER scoring 54. Both the Review filter and the drawer told reviewers the
    opposite in as many words: "missing one puts this candidate below everyone who
    meets them all". A hard gate is pass/fail by definition; anyone who clears every
    gate now ranks above everyone who did not, whatever the scores say.

    Nobody is hidden or rejected by this — it is the ORDER of a list where everyone
    stays listed with their reasons, and any reviewer can still move a gate-failer
    forward with a recorded reason (§6.3).

    Reports and the Review list call this same function: two copies of an ordering
    rule is how an exported shortlist comes to disagree with the screen it was
    exported from.
    """
    return (1 if eligible else 0, BAND_ORDER[fit_band], final_score, candidate_id)

# Recruiter-facing band labels (P33 sweep): "… match" wording everywhere a band
# is shown or exported, so bands never collide with the decision label "Not a fit".
BAND_LABEL: dict[str, str] = {
    "Strong": "Strong match", "Possible": "Possible match", "Not a fit": "Weak match",
}


def band_label(fit_band: str, eligible: bool = True) -> str:
    """The one label reviewers and exports see. A gate failure reads 'Gate not
    met' (the INTERNAL band stays 'Not a fit' — contracts unchanged)."""
    if not eligible:
        return "Gate not met"
    return BAND_LABEL.get(fit_band, fit_band)


def confidence_rating(
    extraction_confidence: float | None,
    downgraded_findings: int,
    rules: "ConfidenceRules | None" = None,
) -> Confidence:
    """Documented heuristic (tune with HR feedback, P13; thresholds governed by
    the scoring model since P51 — the literals here are the seeded defaults):

    - extraction_confidence is the extractor's read-quality measure: Document
      Intelligence's mean word confidence for a scan, the text layer's quality
      (localextract.text_quality) for a file read locally. None means the
      python-docx fallback ran — digital text with no measurement, treated as
      0.85 (reliable but unmeasured).
    - each downgraded finding is a place the model over-claimed; a couple is
      noise, more means the whole record deserves skepticism.
    """
    unmeasured = 0.85 if rules is None else rules.unmeasured_extraction_value
    high_at = 0.9 if rules is None else rules.high_requires_at_least
    low_below = 0.75 if rules is None else rules.low_below
    low_over = 2 if rules is None else rules.low_when_downgraded_findings_over
    conf = unmeasured if extraction_confidence is None else extraction_confidence
    if conf >= high_at and downgraded_findings == 0:
        return "high"
    if conf < low_below or downgraded_findings > low_over:
        return "low"
    return "medium"


def _band_for(final: float, band_strong: float, band_possible: float) -> FitBand:
    if final >= band_strong:
        return "Strong"
    if final >= band_possible:
        return "Possible"
    return "Not a fit"


def assemble_score(
    layer1: Layer1Result,
    layer2: Layer2Result | None,
    critic: CriticReview | None,
    *,
    role_title: str,
    jurisdiction: str,
    extraction_confidence: float | None,
    downgraded_findings: int,
    band_strong: float = 75,
    band_possible: float = 50,
    cap_band_if_must_have_missing: str = "Possible",
    preference_adjustments: list[PreferenceAdjustment] | None = None,
    preference_cap: float = PREFERENCE_CAP,
    confidence_rules: "ConfidenceRules | None" = None,
    clamp_min: float = 0.0,
    clamp_max: float = 100.0,
) -> ScoreResult:
    """Combine everything into the ScoreResult the UI and reports show.

    P40: `preference_adjustments` are the applied HR-preference nudges. Their net
    (each clamped to ±2) is folded into the SAME single 0-100 clamp as the Layer-2
    net — so a run with NO applicable preferences (None/empty) adds 0.0 and is
    byte-identical to pre-P40. Preferences never touch eligibility, gates or the
    must-have band cap; the band recomputes from the final score exactly as before.
    """
    # Suspension consequence, applied here so the final score can NEVER include
    # a suspended adjustment regardless of who calls this.
    if layer2 is not None and critic is not None:
        surviving, suspended = apply_critic(layer2, critic)
    else:
        surviving = list(layer2.adjustments) if layer2 else []
        suspended = []

    prefs = list(preference_adjustments or [])
    net = round(sum(a.points for a in surviving), 2)
    # [0, cap]: a preference only ever adds (2026-09-25) — the same floor as
    # pref_match.enforce_pref_bounds, repeated here because this is the last word.
    pref_net = round(sum(max(0.0, min(preference_cap, p.points)) for p in prefs), 2)
    # The fence: since the merge the floor/ceiling are governed (scoring model
    # clamp.min/max); the 0–100 defaults keep every direct caller byte-identical.
    final = max(clamp_min, min(clamp_max, round(layer1.base_score + net + pref_net, 2)))

    rationale_parts: list[str] = []
    if not layer1.eligible:
        band: FitBand = "Not a fit"
        rationale_parts.append("Not a fit (gate): " + "; ".join(layer1.reasons))
    else:
        band = _band_for(final, band_strong, band_possible)
        rationale_parts.append(
            f"Covers {layer1.coverage_score:.0%} of weighted requirements (base {layer1.base_score:.0f})."
        )
        if layer1.must_haves_missing:
            capped: FitBand = cap_band_if_must_have_missing  # type: ignore[assignment]
            missing = ", ".join(layer1.must_haves_missing)
            if BAND_ORDER[band] > BAND_ORDER[capped]:
                # The cap actually pulls the band DOWN (e.g. Strong -> Possible).
                band = capped
                rationale_parts.append(
                    f"Missing must-have(s) {missing} — band capped at {capped}."
                )
            else:
                # Score already lands at/below the cap, so the cap changes nothing
                # — don't claim it was "capped at Possible" when the band is lower.
                rationale_parts.append(
                    f"Missing must-have(s) {missing} — a core requirement isn't evidenced."
                )
        if surviving:
            rationale_parts.append(
                f"{len(surviving)} evidenced signal adjustment(s), net {net:+.1f}."
            )
    if suspended:
        rationale_parts.append(
            f"{len(suspended)} adjustment(s) suspended by the bias critic (excluded from the score, kept in the record)."
        )
    if critic is not None:
        flagged = sum(1 for v in critic.verdict_per_adjustment.values() if v == "flag")
        if flagged:
            rationale_parts.append(f"{flagged} adjustment(s) flagged for reviewer attention.")
    if prefs:
        rationale_parts.append(
            f"{len(prefs)} hiring-preference note(s), net {pref_net:+.1f}."
        )

    return ScoreResult(
        candidate_id=layer1.candidate_id,
        role_title=role_title,
        jurisdiction=jurisdiction,
        layer1=layer1,
        layer2=layer2,
        critic=critic,
        final_score=final,
        fit_band=band,
        confidence=confidence_rating(extraction_confidence, downgraded_findings, confidence_rules),
        summary_rationale=" ".join(rationale_parts),
        preference_adjustments=prefs,
    )
