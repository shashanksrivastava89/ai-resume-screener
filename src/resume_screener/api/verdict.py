"""Who sees the AI's verdict (InfoSec, 2026-10-14; widened 2026-10-21).

The screening engine produces a VERDICT about every candidate: a band, a score, a
confidence, a written summary, the score's arithmetic, which checklist items the
résumé meets and the quotes that show it, strengths and watch-outs, hard-gate
results, a fairness flag, a batch-quality read — and an ORDER. One capability says who
may see it (`verdict_view`), one predicate reads it (`rbac.may_view_verdict`), and this
module is the one place that takes the verdict out of a payload for anyone else.

From 2026-10-14 only HR held `verdict_view`. Since 2026-10-21 (owner's decision) every
role that can open a requisition's candidates holds it — what stays with the
requisition's team is the DECISIONS (api/decision_view.py). The capability, and this
module, remain so a deployment can narrow the verdict again in rbac.yaml without a
release. A hiring manager sees the candidates shared with them; pool-wide reads — the
band tally, the batch quality — are not theirs (`role_for(pool=False)`).

The builders (review.build_review/build_detail, service.role_summary, explorer's
timeline, …) stay unaware: they build the full payload once, and the route or MCP tool
that hands it to a person passes it through here with that person's capabilities. A
field added to a builder later is therefore VISIBLE to non-HR roles until it is named
below — which is why tests/test_verdict_view.py walks every surface as Legal, as an
administrator and as a hiring manager and fails on any verdict key it finds.

What a person without `verdict_view` keeps: who the candidate is (name, or a number in
blind review), where they say they are, the résumé, the comments, and — on a
requisition of their team's — the decision, its note and the hiring manager's take.
Their list is ordered by decision (Move forward, Discuss, Not a fit, then undecided)
where they see decisions, and then by name, and renumbered — the AI's ranking is part
of its verdict, so the order must not carry it either. Decisions are stripped BEFORE
this module runs, so for a viewer who does not see them the order is by name alone.
"""

from __future__ import annotations

from typing import Any, Iterable

from resume_screener import rbac

# Review-list card keys that carry the verdict.
CARD_FIELDS = frozenset({
    "band", "band_label", "score", "confidence", "flag", "chips", "gates",
    "must_have_gaps", "math",
})
# Drawer (candidate detail) keys that carry the verdict.
DETAIL_FIELDS = frozenset({
    "band", "band_label", "gate_warn", "gate_items", "gate_rule", "score",
    "confidence", "sub", "verdict", "flag", "flag_note", "flag_items", "strengths",
    "watchouts", "req_summary", "reqs", "notes", "pref_notes", "score_math",
})
# The review payload's per-band / per-gate tallies (an aggregate over three
# candidates is a per-candidate verdict). `total` stays.
COUNT_FIELDS = frozenset({
    "strong", "possible", "not_a_fit", "needs_look", "gates_passed", "gate_not_met",
})
# Requisition card / detail keys built from the verdict.
ROLE_FIELDS = frozenset({"bands", "batch_quality"})
# The audit timeline's "screened" step.
TIMELINE_STEP_FIELDS = frozenset({"score", "band", "gates"})

# Said wherever a whole surface is the verdict (the Ask-Claude bundle, the chart).
DENIED_MESSAGE = ("This contains the AI's scores for each candidate, which your role "
                  "doesn't see. The candidates and their résumés are on the requisition.")

_DECISION_ORDER = {"forward": 0, "discuss": 1, "not": 2}


def allowed(caps: Iterable[str] | None) -> bool:
    """The one question, asked of the one predicate."""
    return rbac.may_view_verdict(caps or ())


def _neutral_order(cards: list[dict], blind: bool) -> list[dict]:
    """Decision first, then name (or the opaque id in blind review, where the name
    itself is a rank), renumbered from 1. Never the AI's order."""
    def key(card: dict) -> tuple:
        bucket = _DECISION_ORDER.get(str(card.get("decided") or ""), len(_DECISION_ORDER))
        name = "" if blind else str(card.get("name") or "").casefold()
        return (bucket, name, str(card.get("candidate_id") or ""))

    ordered = sorted(cards, key=key)
    for rank, card in enumerate(ordered, start=1):
        card["rank"] = rank
        if blind:
            card["name"] = f"Candidate {rank}"
    return ordered


def cards_for(cards: list[dict], caps: Iterable[str] | None, *,
              blind: bool = False) -> list[dict]:
    """Review cards as this viewer may see them (new dicts; the input is untouched)."""
    if allowed(caps):
        return cards
    stripped = [{k: v for k, v in card.items() if k not in CARD_FIELDS} for card in cards]
    return _neutral_order(stripped, blind)


def review_for(payload: dict[str, Any], caps: Iterable[str] | None, *,
               blind: bool = False) -> dict[str, Any]:
    """The Review payload as this viewer may see it."""
    if allowed(caps):
        return payload
    out = dict(payload)
    if "cards" in out:
        out["cards"] = cards_for(list(out.get("cards") or []), caps, blind=blind)
    counts = payload.get("counts")
    if isinstance(counts, dict):
        out["counts"] = {k: v for k, v in counts.items() if k not in COUNT_FIELDS}
    if "batch_quality" in out:
        out["batch_quality"] = None
    return out


def detail_for(payload: dict[str, Any] | None,
               caps: Iterable[str] | None) -> dict[str, Any] | None:
    """The candidate drawer as this viewer may see it."""
    if payload is None or allowed(caps):
        return payload
    return {k: v for k, v in payload.items() if k not in DETAIL_FIELDS}


def role_for(payload: dict[str, Any], caps: Iterable[str] | None, *,
             pool: bool = True) -> dict[str, Any]:
    """A requisition card or detail as this viewer may see it. `batch_quality` is kept
    as null — the value an unscreened requisition already has, so no client branch.
    `pool=False` (a hiring manager) drops the band tally and the batch quality even
    with the verdict: both read EVERY candidate screened, and a hiring manager sees
    the ones shared with them."""
    if allowed(caps) and pool:
        return payload
    out = {k: v for k, v in payload.items() if k != "bands"}
    if "batch_quality" in out:
        out["batch_quality"] = None
    return out


def timeline_for(payload: dict[str, Any] | None,
                 caps: Iterable[str] | None) -> dict[str, Any] | None:
    """An audit candidate timeline as this viewer may see it."""
    if payload is None or allowed(caps):
        return payload
    out = dict(payload)
    out["steps"] = [
        ({k: v for k, v in step.items() if k not in TIMELINE_STEP_FIELDS}
         if step.get("step") == "screened" else step)
        for step in payload.get("steps") or []
    ]
    return out


def runs_for(payload: dict[str, Any], caps: Iterable[str] | None) -> dict[str, Any]:
    """The Candidates tab's run history as this viewer may see it — how many met every
    hard gate is a tally of the AI's verdict, so it goes; throughput stays."""
    if allowed(caps):
        return payload
    out = dict(payload)
    out["runs"] = [
        ({**entry, "counts": {k: v for k, v in entry["counts"].items() if k != "gate_passers"}}
         if isinstance(entry.get("counts"), dict) else entry)
        for entry in payload.get("runs") or []
    ]
    return out
