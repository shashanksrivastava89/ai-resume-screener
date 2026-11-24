"""Config-to-config conflict detection — the policy hierarchy cascade (Phase 3).

The hierarchy is Jurisdiction (government floor) ⊃ Scoring model (org policy)
⊃ Preferences (individual) ⊃ Checklist (job). A CHILD may never conflict with a
PARENT. The app already derives run-vs-config staleness on read; this adds the
config-vs-config axis the hierarchy needs — also derived, never stored.

Two pieces:

* ``scoring_conflicts`` — which enabled scoring SIGNALS overlap a currently
  in-force jurisdiction BLOCKED pattern (the org would score something a
  jurisdiction forbids). Deterministic phrase/regex overlap against the signal's
  definition. Surfaced as "stale — conflicts with {jurisdiction}" so Legal/HR
  Head reconcile the model. Only ``blocked`` severity (hard legal stops) counts;
  ``warn`` proxies are for prefs/checklist, not signal reconciliation.

* ``blocked_patterns_as_topics`` — the scoring/narrative-staged jurisdiction
  blocked patterns as TopicRules, so the bias critic screens a Layer-2
  adjustment's evidence/rationale against the region's floor too. This is the
  per-req, EVIDENCE-based enforcement ("jurisdiction supersedes, auto-applied
  downstream"): it fires on an actual reference in the candidate reasoning, not
  on a signal description — so new reqs stay compliant regardless of whether the
  org has reconciled the (flagged-stale) scoring model yet.

The lower tiers (preferences, checklist) go stale by the SAME live
re-classification they already run: prefcheck.classify_text re-evaluates against
the current floor on every read, so a scoring-model/jurisdiction change shows up
immediately in a pref's status and a checklist row's critic_level — no stored
flag, nothing to recompute here.
"""

from __future__ import annotations

import re
from pathlib import Path

from resume_screener.models import BlockedPattern, Signal
from resume_screener.scoring_model import ScoringModel, TopicRule, compile_phrases

_RX_CACHE: dict[str, re.Pattern[str]] = {}


def _hits(pattern: BlockedPattern, text: str) -> bool:
    """Stage-agnostic deterministic match of a blocked pattern against text."""
    if pattern.phrases:
        pat = compile_phrases(pattern.phrases)
        if pat is not None and pat.search(text):
            return True
    for rx in pattern.regexes:
        c = _RX_CACHE.get(rx)
        if c is None:
            c = _RX_CACHE[rx] = re.compile(rx, re.IGNORECASE)
        if c.search(text):
            return True
    return False


def _signal_text(sig: Signal) -> str:
    """The text a signal is 'about' — what a jurisdiction block would conflict
    with (the agent-facing definition, else the recruiter description)."""
    return " ".join(filter(None, [sig.display_name, sig.definition or sig.description]))


def scoring_conflicts_for(
    model: ScoringModel, named_patterns: list[tuple[str, list[BlockedPattern]]]
) -> list[dict]:
    """Pure detector (testable): conflicts between the model's enabled signals and
    the given (jurisdiction, blocked_patterns) groups. Only ``blocked`` severity
    counts. Returns [{signal_id, signal_name, jurisdiction, pattern_id,
    pattern_name, reason}]."""
    conflicts: list[dict] = []
    for jurisdiction, patterns in named_patterns:
        for pat in patterns:
            if pat.severity != "blocked":
                continue
            for sig in model.signals:
                if sig.retired:
                    continue
                if _hits(pat, _signal_text(sig)):
                    conflicts.append({
                        "signal_id": sig.id,
                        "signal_name": sig.display_name or sig.id,
                        "jurisdiction": jurisdiction,
                        "pattern_id": pat.id,
                        "pattern_name": pat.name,
                        "reason": pat.reason,
                    })
    return conflicts


def scoring_conflicts(policies_dir: str | Path = "policies", scoring_dir: str | Path = "signals") -> list[dict]:
    """Convenience wrapper: load the current scoring model + every current
    jurisdiction floor (base + overlays) and report standing conflicts. Derived
    on read; safe to call per request."""
    from resume_screener import policy
    from resume_screener.scoring_model import load_scoring_model

    model = load_scoring_model(scoring_dir)
    groups: list[tuple[str, list[BlockedPattern]]] = [
        ("base", policy.base_blocked_patterns(policies_dir))
    ]
    groups += [(p.jurisdiction, p.blocked_patterns) for p in policy._all_overlays(policies_dir)]
    return scoring_conflicts_for(model, groups)


def blocked_patterns_as_topics(patterns: list[BlockedPattern]) -> list[TopicRule]:
    """The scoring/narrative-staged jurisdiction blocked patterns as TopicRules so
    the bias critic screens Layer-2 evidence/rationale against the region's floor.

    Only patterns that carry literal phrases map cleanly to a TopicRule (which has
    no regex field); a regex-only scoring-stage pattern is skipped here (the
    protected-attribute regexes are prefs/checklist-staged and already covered by
    the scoring model's demographic_language topic)."""
    topics: list[TopicRule] = []
    for p in patterns:
        stages = [s for s in p.stages if s in ("scoring", "narrative")]
        if not stages or not p.phrases:
            continue
        topics.append(TopicRule(
            id=f"jurisdiction:{p.id}", name=p.name, reason=p.reason,
            floor=p.floor, stages=stages, phrases=list(p.phrases),
        ))
    return topics
