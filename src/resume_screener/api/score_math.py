"""Score-math explainability + the live example calculator (P51).

Three read-only builders, all plain recruiter language (no "clamp", no "Layer 2",
no signal ids on screen — technical terms ride in `technical_term` tooltips):

  brief_math(rec)                — the compact popover payload on every Review
                                   card; zero extra I/O (the record is already
                                   in hand when build_review runs).
  build_score_math(req, rec, meta) — the full "How this score adds up" trace on
                                   the detail drawer, AS-OF the run's stamped
                                   rubric + scoring-model versions.
  run_preview(model, spec)       — the Scoring page's example candidate: a DRAFT
                                   scoring model + a synthetic candidate through
                                   the REAL pure functions (score_layer1,
                                   enforce_bounds, apply_critic semantics,
                                   assemble_score). Zero model calls, zero
                                   writes, zero cache — show, don't tell.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from resume_screener.scoring import BAND_ORDER, band_label
from resume_screener.scoring_model import (
    ScoringModel,
    load_scoring_model,
    load_scoring_model_version,
)

# Hard input limits for the preview endpoint (422 above these) — a calculator,
# not a batch API.
PREVIEW_MAX_REQUIREMENTS = 50
PREVIEW_MAX_ADJUSTMENTS = 20
PREVIEW_MAX_PREFERENCES = 10


def _humanize_signal(signal_id: str, model: ScoringModel | None = None) -> str:
    """The scoring model's display name when available, else a readable
    fallback — the same rule the Review drawer applies (never a raw id on
    screen)."""
    try:
        sm = model if model is not None else load_scoring_model()
        signal = sm.signal_by_id(signal_id)
        if signal is not None:
            return signal.display_name or signal_id.replace("_", " ").capitalize()
    except Exception:  # noqa: BLE001
        pass
    return signal_id.replace("_", " ").capitalize()


def _surviving_net(score: dict, suspended_ids: set[str]) -> float:
    adjustments = (score.get("layer2") or {}).get("adjustments") or []
    return round(sum(a["points"] for a in adjustments
                     if a["signal_id"] not in suspended_ids), 2)


def _pref_net(score: dict, cap: float) -> float:
    prefs = score.get("preference_adjustments") or []
    return round(sum(max(-cap, min(cap, p.get("points", 0.0))) for p in prefs), 2)


def _clamp_side(before: float, lo: float = 0.0, hi: float = 100.0) -> str | None:
    if before > hi:
        return "high"
    if before < lo:
        return "low"
    return None


def brief_math(rec: dict, model: ScoringModel | None = None) -> dict:
    """The score-tip numbers: base + extras + preferences → final, and whether
    the score fence trimmed anything. Sums always re-add to the stored final."""
    score = rec["score"]
    suspended_ids = {a["signal_id"] for a in rec.get("suspended_adjustments", [])}
    sm = model if model is not None else load_scoring_model()
    base = float(score["layer1"]["base_score"])
    net = _surviving_net(score, suspended_ids)
    pref_net = _pref_net(score, sm.preferences.per_preference_cap_points)
    before = round(base + net + pref_net, 2)
    return {
        "base": base,
        "adjustments": net,
        "suspended_count": len(suspended_ids),
        "preferences": pref_net,
        "before_limits": before,
        "final": float(score["final_score"]),
        "clamped": _clamp_side(before, sm.clamp.min, sm.clamp.max),
        "band_label": band_label(score["fit_band"], bool(score["layer1"].get("eligible", True))),
    }


def _as_of_model(meta: dict | None) -> tuple[ScoringModel, bool]:
    """The scoring model the run scored under; (current model, approximate=True)
    when the stamp is missing or that version file is gone."""
    stamp = str(((meta or {}).get("versions") or {}).get("scoring_model", ""))
    m = re.search(r"v(\d+)", stamp)
    if m and int(m.group(1)) > 0:
        as_of = load_scoring_model_version(int(m.group(1)))
        if as_of is not None:
            return as_of, False
    return load_scoring_model(), not (m and int(m.group(1)) == 0)


def _as_of_rubric(req, meta: dict | None):
    """The exact rubric VERSION the run scored under (run-time truth, not the
    latest checklist); (rubric|None, approximate)."""
    from resume_screener.rubric import load_rubric

    stamp = str(((meta or {}).get("versions") or {}).get("rubric", ""))
    m = re.match(r"(.+\.rubric\.v\d+\.yaml)", stamp)
    if m:
        path = Path("jds") / m.group(1)
        if path.exists():
            try:
                return load_rubric(path), False
            except Exception:  # noqa: BLE001
                pass
    from resume_screener.api import service

    return service.rubric_for(req), True


def _base_lines(rubric, findings: list[dict], model: ScoringModel) -> list[dict]:
    """Per-requirement arithmetic: importance share × credit = points. Hard gates
    carry no points (they gate, they don't score) so they're listed with the band
    instead, not here."""
    if rubric is None:
        return []
    match_of = {f["requirement_id"]: f["match"] for f in findings}
    lines = []
    for r in rubric.normalized().requirements:
        if r.kind == "hard_gate":
            continue
        match = match_of.get(r.id, "none")
        credit = model.coverage.match_value.get(match, 0.0)
        lines.append({
            "label": r.text,
            "kind": r.kind,
            "weight_pct": round(r.weight * 100, 1),
            "credit": match,
            "credit_value": credit,
            "points": round(100 * r.weight * credit, 1),
        })
    return lines


def _confidence_explainer(level: str, extraction_confidence: float | None,
                          downgraded: int) -> str:
    if level == "high":
        return "Read cleanly, and every claim survived the evidence check."
    if level == "low":
        if downgraded > 2:
            return ("Several claims had to be corrected against the résumé text — "
                    "double-check the evidence before deciding.")
        return ("This résumé was hard to read (likely scanned or photographed) — "
                "double-check the evidence before deciding.")
    if extraction_confidence is None:
        return "A digital document with no scan-quality measurement — treated as reliable."
    return "Read reasonably well; worth a normal level of care."


def build_score_math(req, rec: dict, meta: dict | None) -> dict:
    """The full trace for the drawer's "How this score adds up" section."""
    from resume_screener.scoring_model import resolved_display

    score = rec["score"]
    layer1 = score["layer1"]
    model, model_approx = _as_of_model(meta)
    rubric, rubric_approx = _as_of_rubric(req, meta)
    display = resolved_display(model)

    suspended_ids = {a["signal_id"] for a in rec.get("suspended_adjustments", [])}
    critic_verdicts = ((score.get("critic") or {}).get("verdict_per_adjustment") or {})
    adjustments = (score.get("layer2") or {}).get("adjustments") or []
    net = _surviving_net(score, suspended_ids)
    pref_cap = model.preferences.per_preference_cap_points
    pref_net = _pref_net(score, pref_cap)
    base = float(layer1["base_score"])
    before = round(base + net + pref_net, 2)
    final = float(score["final_score"])
    eligible = bool(layer1.get("eligible", True))
    clamp_lo, clamp_hi = model.clamp.min, model.clamp.max

    # Would the band have been higher without the must-have cap?
    stored_band = score["fit_band"]
    uncapped = ("Strong" if final >= model.bands.strong_at
                else "Possible" if final >= model.bands.possible_at else "Not a fit")
    capped_by_must_have = (
        uncapped if (eligible and layer1.get("must_haves_missing")
                     and BAND_ORDER.get(uncapped, 0) > BAND_ORDER.get(stored_band, 0))
        else None)

    downgraded = sum(1 for f in rec["findings"]["findings"]
                     if "Downgraded" in (f.get("note") or ""))
    extraction_conf = (rec.get("extraction") or {}).get("confidence")

    return {
        "as_of": {
            "scoring_model_version": model.version,
            "checklist": str(((meta or {}).get("versions") or {}).get("rubric", "")) or None,
            "approximate": bool(model_approx or rubric_approx),
        },
        "base": {
            "total": base,
            "explainer": (f"Covers {round(float(layer1['coverage_score']) * 100)}% of the "
                          "weighted checklist — that's where the score starts."),
            "lines": _base_lines(rubric, rec["findings"]["findings"], model),
        },
        "adjustments": {
            "net": net,
            "cap_total": model.adjustments.total_cap_points,
            "cap_note": display.get("total_cap_note", ""),
            "lines": [{
                "label": _humanize_signal(a["signal_id"], model),
                "points": round(a["points"], 1),
                "flagged": critic_verdicts.get(a["signal_id"]) == "flag",
            } for a in adjustments if a["signal_id"] not in suspended_ids],
            "suspended": [{
                "label": _humanize_signal(a["signal_id"], model),
                "points": round(a["points"], 1),
                "note": "Set aside by the fairness check — not counted.",
            } for a in rec.get("suspended_adjustments", [])],
            "enforcement_notes": rec.get("layer2_enforcement", []) or [],
        },
        "preferences": {
            "net": pref_net,
            "per_item_cap": pref_cap,
            "cap_note": display.get("preference_cap_note", ""),
            "lines": [{"label": p.get("text", ""), "points": round(p.get("points", 0), 1)}
                      for p in score.get("preference_adjustments", [])],
        },
        "total": {"before_limits": before, "final": final,
                  "clamped": _clamp_side(before, clamp_lo, clamp_hi)},
        "band": {
            "result": stored_band,
            "label": band_label(stored_band, eligible),
            "strong_at": model.bands.strong_at,
            "possible_at": model.bands.possible_at,
            "clamp_min": clamp_lo,
            "clamp_max": clamp_hi,
            "capped_by_must_have": capped_by_must_have,
            "cap_note": display.get("band_cap_note", "") if capped_by_must_have else None,
            "gate_note": (None if eligible else
                          "A hard requirement wasn't met, so the rating reads "
                          "\"Gate not met\" whatever the number says."),
        },
        "confidence": {
            "level": score["confidence"],
            "explainer": _confidence_explainer(score["confidence"], extraction_conf,
                                               downgraded),
        },
    }


# --- the live example calculator -------------------------------------------------------


class PreviewError(ValueError):
    """Bad calculator input — plain message, maps to HTTP 422."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PreviewError(message)


def run_preview(model: ScoringModel, spec: dict) -> dict:
    """One synthetic candidate through the real pure scoring functions under a
    DRAFT scoring model. Returns {result, math-ish sections, waterfall}."""
    from resume_screener.findings import FindingsRecord
    from resume_screener.layer1 import passes_gate, score_layer1
    from resume_screener.layer2 import AdjustmentDraft, enforce_bounds
    from resume_screener.models import (
        CriticReview, EnabledSignal, Finding, Layer2Result, PreferenceAdjustment,
        Requirement, Rubric,
    )
    from resume_screener.scoring import assemble_score

    reqs_in = list(spec.get("requirements") or [])
    adjs_in = list(spec.get("adjustments") or [])
    prefs_in = list(spec.get("preferences") or [])
    _require(len(reqs_in) >= 1, "Give the example candidate at least one requirement.")
    _require(len(reqs_in) <= PREVIEW_MAX_REQUIREMENTS,
             f"At most {PREVIEW_MAX_REQUIREMENTS} requirements in the calculator.")
    _require(len(adjs_in) <= PREVIEW_MAX_ADJUSTMENTS,
             f"At most {PREVIEW_MAX_ADJUSTMENTS} extras in the calculator.")
    _require(len(prefs_in) <= PREVIEW_MAX_PREFERENCES,
             f"At most {PREVIEW_MAX_PREFERENCES} preferences in the calculator.")

    # 1) Layer 1 over a synthetic rubric + findings (weights renormalize on load,
    #    exactly like a real checklist).
    requirements, findings = [], []
    for i, r in enumerate(reqs_in):
        kind = str(r.get("kind", "nice_to_have"))
        _require(kind in ("must_have", "nice_to_have", "hard_gate"),
                 f"Unknown requirement kind '{kind}'.")
        match = str(r.get("match", "none"))
        _require(match in ("full", "partial", "none"), f"Unknown match level '{match}'.")
        weight = float(r.get("weight", 1) or 0)
        _require(0 <= weight <= 100, "Requirement importance must be between 0 and 100.")
        rid = f"r{i:02d}"
        label = str(r.get("label") or f"Requirement {i + 1}")
        requirements.append(Requirement(id=rid, text=label, type="skill",
                                        kind=kind, weight=weight))
        findings.append(Finding(requirement_id=rid, requirement_text=label,
                                match=match, evidence=None))
    rubric = Rubric(role_title="Example candidate", jd_ref="preview", version=1,
                    requirements=requirements)
    record = FindingsRecord(candidate_id="preview", role_title="Example candidate",
                            rubric_version=1, findings=findings)
    layer1 = score_layer1(record, rubric, match_value=model.coverage.match_value)

    # 2) The extras through the REAL enforce_bounds (direction rule, per-factor
    #    cap, total cap with proportional scaling). The synthetic evidence tokens
    #    legitimately satisfy rule 4's verbatim check.
    enabled, drafts, verdicts, label_of = [], [], {}, {}
    tokens = []
    for i, a in enumerate(adjs_in):
        sid = f"factor_{i:02d}"
        label = str(a.get("label") or f"Extra {i + 1}")
        direction = str(a.get("direction", "both"))
        _require(direction in ("plus", "minus", "both"), f"Unknown direction '{direction}'.")
        cap = float(a.get("cap_points", 4) or 0)
        _require(cap > 0, "A factor's limit must be above zero.")
        points = float(a.get("points", 0) or 0)
        _require(abs(points) <= 1000, "Keep example points within ±1000.")
        verdict = str(a.get("verdict", "pass"))
        _require(verdict in ("pass", "flag", "suspend"), f"Unknown verdict '{verdict}'.")
        token = f"[{sid}]"
        tokens.append(token)
        enabled.append(EnabledSignal(id=sid, description=label, definition=label,
                                     kind="judged", direction=direction,
                                     cap_points=cap, enabled_default=True,
                                     retired=False, job_relatedness="preview",
                                     params={}))
        drafts.append(AdjustmentDraft(signal_id=sid, points=points,
                                      evidence=token, rationale=label))
        verdicts[sid] = verdict
        label_of[sid] = label
    kept, enforcement_notes = enforce_bounds(
        drafts, enabled, " ".join(tokens), model.adjustments.total_cap_points)
    layer2 = Layer2Result(candidate_id="preview", adjustments=kept,
                          proposed_signals=[], narrative="preview")
    critic = CriticReview(verdict_per_adjustment={a.signal_id: verdicts[a.signal_id]
                                                  for a in kept},
                          notes=[], overall_flags=[])

    # 3) Preferences (each clamped to the draft cap inside assemble_score).
    pref_adjustments = []
    for i, p in enumerate(prefs_in):
        points = float(p.get("points", 0) or 0)
        _require(abs(points) <= 1000, "Keep example points within ±1000.")
        pref_adjustments.append(PreferenceAdjustment(
            pref_id=f"p{i:02d}", owner="preview",
            text=str(p.get("label") or f"Preference {i + 1}"),
            points=points, evidence=None, rationale="preview"))

    extraction_confidence = spec.get("extraction_confidence")
    if extraction_confidence is not None:
        extraction_confidence = float(extraction_confidence)
        _require(0 <= extraction_confidence <= 1, "Read quality is between 0 and 1.")
    downgraded = int(spec.get("downgraded_findings", 0) or 0)
    _require(0 <= downgraded <= 100, "Corrected-claim count is between 0 and 100.")

    # 4) The real final assembly under the DRAFT model.
    score = assemble_score(
        layer1, layer2, critic,
        role_title="Example candidate", jurisdiction="preview",
        extraction_confidence=extraction_confidence,
        downgraded_findings=downgraded,
        band_strong=model.bands.strong_at,
        band_possible=model.bands.possible_at,
        cap_band_if_must_have_missing=model.bands.cap_if_must_have_missing,
        preference_adjustments=pref_adjustments,
        preference_cap=model.preferences.per_preference_cap_points,
        confidence_rules=model.confidence,
        clamp_min=model.clamp.min, clamp_max=model.clamp.max,
    )

    # 5) The waterfall: base → each extra (with its cap/set-aside story) → each
    #    preference → limits → band. Built from the REAL enforced objects.
    suspended_ids = {sid for sid, v in verdicts.items() if v == "suspend"}
    kept_points = {a.signal_id: a.points for a in kept}
    running = layer1.base_score
    waterfall: list[dict] = [{
        "step": "base", "label": "Checklist coverage",
        "delta": layer1.base_score, "running": running, "note": None,
    }]
    for draft in drafts:
        sid = draft.signal_id
        if sid in suspended_ids and sid in kept_points:
            waterfall.append({"step": "suspended", "label": label_of[sid],
                              "delta": 0.0, "running": round(running, 2),
                              "note": "set aside by the fairness check — not counted"})
            continue
        points = kept_points.get(sid)
        if points is None:
            waterfall.append({"step": "dropped", "label": label_of[sid],
                              "delta": 0.0, "running": round(running, 2),
                              "note": "didn't survive the checks (direction or zero points)"})
            continue
        running = round(running + points, 2)
        note = None
        if abs(points) < abs(draft.points):
            note = f"reduced from {draft.points:+g} to {points:+g} (its limit)"
        waterfall.append({"step": "adjustment", "label": label_of[sid],
                          "delta": points, "running": running, "note": note})
    cap = model.preferences.per_preference_cap_points
    for p in pref_adjustments:
        # [0, cap] — the engine's rule (scoring.assemble_score): a preference only adds.
        clamped = max(0.0, min(cap, p.points))
        running = round(running + clamped, 2)
        if p.points < 0:
            note = f"a preference only adds — {p.points:+g} counts as 0"
        elif clamped != p.points:
            note = f"reduced from {p.points:+g} to {clamped:+g} (the preference limit)"
        else:
            note = None
        waterfall.append({"step": "preference", "label": p.text,
                          "delta": clamped, "running": running, "note": note})
    if _clamp_side(running, model.clamp.min, model.clamp.max):
        waterfall.append({"step": "clamp",
                          "label": (f"Score stays between {model.clamp.min:g} "
                                    f"and {model.clamp.max:g}"),
                          "delta": round(score.final_score - running, 2),
                          "running": score.final_score, "note": None})
        running = score.final_score
    band_note = None
    if not layer1.eligible:
        band_note = "a hard requirement wasn't met → \"Gate not met\""
    elif layer1.must_haves_missing and score.fit_band == model.bands.cap_if_must_have_missing:
        band_note = f"a must-have is missing → capped at {model.bands.cap_if_must_have_missing}"
    waterfall.append({
        "step": "band",
        "label": (f"{score.final_score:g} ≥ {model.bands.strong_at:g} → Strong match"
                  if score.fit_band == "Strong" and layer1.eligible else
                  f"{score.final_score:g} ≥ {model.bands.possible_at:g} → Possible match"
                  if score.fit_band == "Possible" and layer1.eligible else
                  f"{score.final_score:g} → {band_label(score.fit_band, layer1.eligible)}"),
        "delta": 0.0, "running": score.final_score, "note": band_note,
    })

    return {
        "result": {
            "final_score": score.final_score,
            "band": score.fit_band,
            "band_label": band_label(score.fit_band, layer1.eligible),
            "eligible": layer1.eligible,
            "gate_passer": passes_gate(layer1),
            "confidence": score.confidence,
            "summary": score.summary_rationale,
        },
        "base": {"total": layer1.base_score, "coverage_pct": round(layer1.coverage_score * 100)},
        "enforcement_notes": enforcement_notes,
        "waterfall": waterfall,
    }
