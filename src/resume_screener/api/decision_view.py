"""Who sees the hiring decisions (2026-10-21, owner's decision).

A requisition's DECISIONS — Move forward, Discuss, Not a fit, the note that goes with
each, who made it and when, and the hiring manager's take — belong to the people
working that requisition: the recruiter who owns it, anyone it is shared with to
collaborate, its hiring manager, and HR Heads (`decisions_all`). Everyone else who can
open it — Legal reading it from the audit trail, an administrator — sees every
candidate and the AI's read on them (api/verdict.py), but not what the team decided.

`app._sees_decisions` is the one predicate. It reuses `_req_is_mine`, the ownership
rule that already decides who works a requisition, so the people who can work it are
the people who see its decisions. This module is the one place that takes decisions
out of a payload; the builders (review.build_review/build_detail, handoff) stay
unaware, as they do for the verdict. A payload always says which way it went
(`decisions_visible`), so the client hides the decision controls rather than showing
everyone as "awaiting" — an absent decision must never read as "nobody decided".

Strip decisions BEFORE the verdict: when a deployment narrows `verdict_view`, the
verdict module orders the list by decision, and that order must not carry decisions
the viewer is not shown.

Not here, on purpose: Audit & History. Its feed and timelines are the record of who
decided what, which is why Legal and administrators hold `runs_audit` at all.
"""

from __future__ import annotations

from typing import Any

# Review-list card keys that carry the team's decisions.
CARD_FIELDS = frozenset({
    "decided", "decided_reason", "decided_ts", "decided_by", "decided_reconfirm",
    "hm_take",
})
# Drawer (candidate detail) keys that carry them.
DETAIL_FIELDS = frozenset({"decision", "hm_take"})
# Review-payload keys built from them: the per-decision tallies, and which
# candidates' decisions changed since the shortlist went to the hiring manager.
PAYLOAD_FIELDS = frozenset({"decision_counts"})

HIDDEN_MESSAGE = ("Decisions on this requisition are shown to its team — the recruiter "
                  "who owns it, the people it's shared with, its hiring manager and "
                  "HR Heads.")
DECIDE_MESSAGE = ("Decisions on this requisition are made by its team — the recruiter "
                  "who owns it, the people it's shared with, and HR Heads.")


def review_for(payload: dict[str, Any], visible: bool) -> dict[str, Any]:
    """The Review payload as this viewer may see it (a new dict)."""
    out = dict(payload)
    out["decisions_visible"] = visible
    if visible:
        return out
    out["decisions_note"] = HIDDEN_MESSAGE   # the client says this; it holds no copy
    if "cards" in out:
        out["cards"] = [{k: v for k, v in card.items() if k not in CARD_FIELDS}
                        for card in payload.get("cards") or []]
    for key in PAYLOAD_FIELDS:
        out.pop(key, None)
    if "hm_updates" in out:
        out["hm_updates"] = None
    return out


def detail_for(payload: dict[str, Any] | None, visible: bool) -> dict[str, Any] | None:
    """The candidate drawer as this viewer may see it (a new dict)."""
    if payload is None:
        return None
    out = {k: v for k, v in payload.items() if visible or k not in DETAIL_FIELDS}
    out["decisions_visible"] = visible
    if not visible:
        out["decisions_note"] = HIDDEN_MESSAGE
    return out
