"""Layer 1 (P8, §5.2) — the deterministic half of the score. The audit answer.

What makes this layer special
-----------------------------
PURE code over frozen artifacts: no model calls, no I/O, no clock, no config
reads. Given the same findings artifact and the same rubric version, it returns
the identical Layer1Result — months later, on another machine. When an auditor
or a candidate asks "why this score?", this layer's output is the part we can
re-derive on demand, line by line. Keep it that way: if a change here needs a
network call or wall-clock time, it belongs somewhere else.

The three steps (in order)
--------------------------
1. HARD GATES — requirements with kind="hard_gate": objective pass/fail
   conditions from the JD. A gate passes only when its finding is exactly
   "full" (explicitly satisfied — partial evidence fails a gate). Failing one
   sets eligible=False and flags it — but the candidate STAYS LISTED with
   reasons. Nothing is ever hidden (§6.3). Gates contribute 0 to coverage.
2. MUST-HAVE GATE — a must-have with a "none" finding goes into
   must_haves_missing. "partial" does NOT count as missing (it earns half
   credit in coverage instead); missing means zero evidence at all.
3. WEIGHTED COVERAGE — coverage = Σ weight_i × value_i with full=1.0,
   partial=0.5, none=0.0 over the non-gate requirements (must + nice),
   weights normalized to sum 1. base_score = 100 × coverage.

Who gets Layer 2 ("gate-passers only", §5.4)
--------------------------------------------
passes_gate() below is THE definition: eligible AND no missing must-haves.
Layer 2 spends model calls refining candidates who could plausibly be hired;
gate failures and zero-evidence-on-a-must-have candidates keep their
Layer-1 score and reasons instead. This is also the §5 cost model — L2 + critic
run on the 40-60% of the pool that passes, not everyone.
"""

from __future__ import annotations

from typing import Mapping

from resume_screener.findings import FindingsRecord
from resume_screener.models import Finding, Layer1Result, Rubric

# The match->credit table (§5.2). Changing these values changes every score —
# that's a governance decision, which is why the live values come from the
# scoring model (signals/scoring_model.vN.yaml) via the caller; this constant
# is the documented default the model was seeded from.
MATCH_VALUE: dict[str, float] = {"full": 1.0, "partial": 0.5, "none": 0.0}


class Layer1Error(RuntimeError):
    """Artifact/rubric integrity problem — the inputs can't be scored as-is.
    Raised loudly instead of guessing, because Layer 1's whole value is that
    its numbers are beyond doubt.

    2026-07-30 root-cause fix: this used to also fire whenever
    `record.rubric_version != rubric.version` — comparing a PER-REQUISITION edit
    counter that has no meaning across requisitions. Findings are cached by
    CONTENT (résumé text + the exact requirement set — see
    findings.compute_findings), so the SAME candidate_id legitimately reuses a
    cached finding across different requisitions (or an earlier version of the
    same one) whenever the requirement wording is unchanged — e.g. two reqs
    drafted from the same JD, or a rubric edit that only reweights an existing
    row. The version-counter check couldn't tell that apart from a genuine
    content change, so it refused ~every candidate in a batch that happened to
    share requirement text with another requisition (a live incident: 304 of
    305 candidates on one req were skipped because their findings — correctly
    cached, correctly re-derived from the SAME wording — carried a foreign
    req's version stamp). The check now compares the actual requirement TEXT
    stored on each Finding instead (see finding_for below), which is the thing
    that must not have silently changed."""


def _findings_by_id(record: FindingsRecord) -> dict[str, Finding]:
    by_id: dict[str, Finding] = {}
    for finding in record.findings:
        by_id.setdefault(finding.requirement_id, finding)  # first wins; artifacts shouldn't have dupes
    return by_id


def score_layer1(
    record: FindingsRecord,
    rubric: Rubric,
    match_value: Mapping[str, float] | None = None,
) -> Layer1Result:
    """Score one candidate's frozen findings against the rubric. Pure function.
    `match_value` comes from the governed scoring model; None = the defaults."""
    credit = MATCH_VALUE if match_value is None else match_value
    rubric = rubric.normalized()  # weights sum to 1 no matter what the YAML says

    by_id = _findings_by_id(record)

    def finding_for(item_id: str, text: str, what: str) -> Finding:
        finding = by_id.get(item_id)
        if finding is None:
            raise Layer1Error(
                f"Artifact for candidate {record.candidate_id} has no finding for "
                f"{what} '{item_id}' — the artifact is incomplete; re-run findings."
            )
        # Replay integrity, checked on CONTENT rather than the rubric's version
        # counter (2026-07-30 — see the note above Layer1Error's definition for
        # why the version counter was the wrong signal). A finding is safe to
        # score against this requirement whenever it was made against the SAME
        # wording; the id/version numbering it arrived under doesn't matter.
        if finding.requirement_text != text:
            raise Layer1Error(
                f"Artifact for candidate {record.candidate_id} has a finding for "
                f"{what} '{item_id}' against different wording than the current "
                f"rubric ({finding.requirement_text!r} vs {text!r}) — the "
                "requirement changed; re-run findings."
            )
        return finding

    # One ordered pass over the single requirements list (P33). Gates first in
    # spirit: "full" is the only passing grade for a hard gate — it is an
    # explicit JD condition, so ambiguity ("partial") does not satisfy it.
    # Gates carry weight 0 after normalization, so they contribute 0 coverage.
    gate_flags: list[str] = []
    reasons: list[str] = []
    coverage = 0.0
    must_haves_missing: list[str] = []
    for requirement in rubric.requirements:
        finding = finding_for(requirement.id, requirement.text, "requirement")
        if requirement.kind == "hard_gate":
            if finding.match != "full":
                gate_flags.append(requirement.id)
                reasons.append(f"Hard requirement not met: {requirement.text}")
            continue
        coverage += requirement.weight * credit[finding.match]
        if requirement.kind == "must_have" and finding.match == "none":
            must_haves_missing.append(requirement.id)
            reasons.append(f"Must-have not evidenced: {requirement.text}")
    eligible = not gate_flags

    # Rounding keeps artifacts byte-identical across replays (floats formatted
    # from the same math always agree, but rounding makes diffs human-friendly).
    coverage = round(coverage, 4)

    return Layer1Result(
        candidate_id=record.candidate_id,
        rubric_version=rubric.version,
        gate_flags=gate_flags,
        must_haves_missing=must_haves_missing,
        coverage_score=coverage,
        base_score=round(100 * coverage, 2),
        eligible=eligible,
        reasons=reasons,
    )


def passes_gate(result: Layer1Result) -> bool:
    """THE gate definition (§5.4): who proceeds to Layer 2.

    Centralized here so Layer 2, the run pipeline and the UI can never drift
    apart on what 'gate-passer' means."""
    return result.eligible and not result.must_haves_missing
