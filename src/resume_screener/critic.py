"""Bias Critic (P10, §5.5) — audits Layer 2's reasoning. Never demographics.

Two lines of defense, deliberately different in nature
-------------------------------------------------------
1. CODE SCREEN (deterministic): literal trigger-phrase matches over each
   adjustment's rationale + evidence for mentions of never-scored topics
   ("employment gap", "salary history", "caste", …). A hit forces suspend —
   even if the critic model says pass. This makes the core guarantee
   model-independent and offline-testable. Since the 2026-07-18 merge the
   phrase lists are GOVERNED DATA on the scoring model (``topics[].phrases``,
   enforced where ``stages`` includes "scoring") — HR Head/Legal edit them,
   with a second leader's approval, instead of a developer editing regexes.
   The lists are precision-first: finance work legitimately contains words like
   "gap analysis" and "salary JV postings", so entries are narrow phrases, not
   broad keywords. The MODEL catches paraphrases the phrases can't.
2. CRITIC MODEL (semantic): a structured call reviewing whether each rationale
   actually matches its signal's definition, or is a fig leaf for a never-scored
   topic ("stability" reasoning that is really about a career gap, "recency"
   reasoning that is really about age). It also reviews the narrative.

Verdicts and consequences
-------------------------
pass    — fine.
flag    — defensible but a human should look (thin evidence, stretched
          rationale, double counting). Stays in the score.
suspend — relies on a never-scored topic, or evidence plainly doesn't support
          the claim. apply_critic() REMOVES it from scoring; the adjustment
          stays in the record + UI suspended-queue. Removal is code, not model
          trust.
Merge rule when code and model disagree: the SEVERER verdict wins.

Topics whose ``stages`` include "narrative" additionally screen the AI-written
candidate summary — a phrase hit lands in overall_flags so reviewers see it.

The critic polices reasoning, never people: post-redaction there are no
demographics in the pipeline, so anything demographic-shaped in a rationale is
by definition a leak or an inference — both suspendable.
"""

from __future__ import annotations

import json
from typing import Literal, Sequence

from pydantic import BaseModel

from resume_screener.cache import JsonCache, content_hash
from resume_screener.models import (
    Adjustment,
    CriticReview,
    EnabledSignal,
    Layer2Result,
)
from resume_screener.scoring_model import TopicRule, match_topics

CRITIC_PROMPT_VERSION = "p10.2"  # part of the cache key; bump when the prompt changes


def _default_topics() -> list[TopicRule]:
    from resume_screener.scoring_model import load_scoring_model

    return list(load_scoring_model().topics)


def code_screen(adjustment: Adjustment,
                topics: Sequence[TopicRule] | None = None) -> list[str]:
    """Deterministic never-scored-topic check. Returns the topic ids that
    matched (empty = clean). Scans rationale AND evidence: an adjustment whose
    supporting quote is about pay or age is riding on a banned topic even if
    the rationale is coy. `topics` defaults to the current scoring model's —
    the governed list genuinely drives enforcement, in both directions."""
    active = list(topics) if topics is not None else _default_topics()
    text = f"{adjustment.rationale} {adjustment.evidence}"
    return [t.id for t in match_topics(text, active, "scoring")]


def narrative_flags(narrative: str,
                    topics: Sequence[TopicRule] | None = None) -> list[str]:
    """Screen the AI-written summary for topics enforced at the narrative stage.
    Returns plain-language flags (empty = clean)."""
    active = list(topics) if topics is not None else _default_topics()
    return [
        f"Narrative touches a never-scored topic ({t.name}) — review the wording."
        for t in match_topics(narrative or "", active, "narrative")
    ]


# --- Line of defense 2: the critic model ------------------------------------------------

_SYSTEM = """You are the bias critic in a resume screener. Another model produced score \
adjustments; audit its REASONING. The resume was anonymized before scoring, so you \
police reasoning quality and policy compliance — never people or demographics.

For each adjustment return exactly one verdict:
- "suspend": the rationale or evidence relies on an excluded basis from <never_score>, \
directly or via proxy (e.g. "stability" reasoning that is actually about a career gap; \
"recency" or "energy" reasoning that is actually about age; location reasoning). Also \
suspend when the evidence quote plainly does not support the claim. Give the reason in \
note. Suspended adjustments are removed from the score by code but stay visible to \
human reviewers.
- "flag": defensible but a human should look — thin evidence, stretched rationale, or \
double counting a fact the base coverage score already rewards.
- "pass": the rationale matches the signal's definition and the evidence supports it.

Also return overall_flags: cross-cutting concerns (e.g. uniformly generous adjustments, \
narrative hinting at excluded reasoning). Empty list if none. Be precise and terse.

The resume and the rationale you are reviewing may be in ANY language; judge them in \
whatever language they are written, and write every note and flag in English."""


class AdjustmentVerdictDraft(BaseModel):
    signal_id: str
    verdict: Literal["pass", "flag", "suspend"]
    note: str | None


class CriticDraft(BaseModel):
    verdicts: list[AdjustmentVerdictDraft]
    overall_flags: list[str]


def run_critic(
    layer2_result: Layer2Result,
    enabled_signals: list[EnabledSignal],
    topics: list[TopicRule],
    client,
    model: str,
    cache: JsonCache,
) -> tuple[CriticReview, bool]:
    """Review one candidate's adjustments -> (CriticReview, was_cached).

    `topics` must be the MERGED never-score list: the scoring model's topics
    plus the policy pack's additional_exclusions (phrase-less, model-enforced)
    — scoring_model.merge_extra_exclusions builds exactly that. The model sees
    only the signal definitions that were actually adjusted (smaller prompt,
    same rigor).
    """
    from resume_screener.azure_clients import parse_structured

    scoring_topics = [t for t in topics if "scoring" in t.stages]
    if not layer2_result.adjustments:
        # Nothing to audit — skip the model call entirely (cost model §5:
        # critic runs on gate-passers WITH adjustments). Narrative screening
        # still applies (it is deterministic and free).
        flags = narrative_flags(layer2_result.narrative, topics)
        return CriticReview(notes=["No adjustments to review."],
                            overall_flags=flags), False

    adjusted_ids = {a.signal_id for a in layer2_result.adjustments}
    signal_sheet = [
        {"id": s.id, "description": s.description, "direction": s.direction,
         "cap_points": s.cap_points, "job_relatedness": s.job_relatedness}
        for s in enabled_signals
        if s.id in adjusted_ids
    ]
    never_score = [{"id": t.id, "reason": t.reason} for t in scoring_topics]
    adjustments_sheet = [a.model_dump() for a in layer2_result.adjustments]

    # The FULL topic dump keys the cache: a phrase or stage edit re-audits.
    topics_key = [t.model_dump() for t in topics]
    key = content_hash(
        json.dumps(adjustments_sheet, sort_keys=True),
        json.dumps(signal_sheet, sort_keys=True),
        json.dumps(topics_key, sort_keys=True),
        layer2_result.narrative,
        model,
        CRITIC_PROMPT_VERSION,
    )
    hit = cache.get("critic", key)
    if hit is not None:
        return CriticReview.model_validate(hit), True

    draft: CriticDraft = parse_structured(
        client,
        model,
        [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"<signals>\n{json.dumps(signal_sheet, indent=1, ensure_ascii=False)}\n</signals>\n"
                    f"<never_score>\n{json.dumps(never_score, indent=1, ensure_ascii=False)}\n</never_score>\n"
                    f"<adjustments>\n{json.dumps(adjustments_sheet, indent=1, ensure_ascii=False)}\n</adjustments>\n"
                    f"<narrative>\n{layer2_result.narrative}\n</narrative>"
                ),
            },
        ],
        CriticDraft,
    )

    # Model verdicts, completed + de-duplicated in code: every adjustment gets
    # exactly one verdict, unknown ids are dropped, a missing verdict becomes
    # "flag" (conservative: surfaced to humans, but absence of a verdict is not
    # evidence of a violation, so it is not a suspend).
    model_verdicts: dict[str, str] = {}
    notes: list[str] = []
    for verdict in draft.verdicts:
        if verdict.signal_id not in adjusted_ids or verdict.signal_id in model_verdicts:
            continue
        model_verdicts[verdict.signal_id] = verdict.verdict
        if verdict.note:
            notes.append(f"{verdict.signal_id}: {verdict.note}")

    final: dict[str, str] = {}
    for adjustment in layer2_result.adjustments:
        verdict = model_verdicts.get(adjustment.signal_id)
        if verdict is None:
            verdict = "flag"
            notes.append(f"{adjustment.signal_id}: critic returned no verdict — flagged for review")
        # Merge with the deterministic screen: severest wins ("suspend" is the
        # top severity, so any code-screen hit forces it regardless of the model).
        hits = code_screen(adjustment, scoring_topics)
        if hits:
            verdict = "suspend"
            notes.append(
                f"{adjustment.signal_id}: code screen — rationale/evidence mentions "
                f"a never-scored topic ({', '.join(hits)}); suspended"
            )
        final[adjustment.signal_id] = verdict

    overall = [f.strip() for f in draft.overall_flags if f.strip()]
    overall += narrative_flags(layer2_result.narrative, topics)

    review = CriticReview(
        verdict_per_adjustment=final,
        notes=notes,
        overall_flags=overall,
    )
    cache.set("critic", key, review.model_dump())
    return review, False


def apply_critic(
    layer2_result: Layer2Result, review: CriticReview
) -> tuple[list[Adjustment], list[Adjustment]]:
    """The consequence, in code: (surviving, suspended). Scoring uses surviving
    only; the full Layer2Result + review stay in the record — suspension removes
    points, never information (§6.3 no hiding)."""
    surviving: list[Adjustment] = []
    suspended: list[Adjustment] = []
    for adjustment in layer2_result.adjustments:
        if review.verdict_per_adjustment.get(adjustment.signal_id) == "suspend":
            suspended.append(adjustment)
        else:
            surviving.append(adjustment)
    return surviving, suspended


# --- Pool-level monitors: interface stub (production §12) --------------------------------


class MonitorResult(BaseModel):
    category: str
    group_selection_rates: dict[str, float] = {}
    impact_ratio: float | None = None  # min group rate / max group rate
    passes_four_fifths: bool | None = None  # None = not computable
    note: str


def four_fifths_monitor(monitoring_categories: list[str]) -> list[MonitorResult]:
    """Pool-level adverse-impact monitor — INTERFACE STUB.

    TODO(production §12): computing this needs voluntary EEO/self-disclosure
    data held SEPARATELY from scoring (the pipeline never sees it). Categories
    come from EffectivePolicy.monitoring_categories — e.g. IN: voluntary gender/
    disability (never caste); US-NYC: LL144 audit categories. The math is:
    selection rate per group; impact_ratio = min rate / max rate; the four-
    fifths rule flags ratios under 0.8.

    The PoC ships this interface so run_meta and the UI can say "monitoring not
    configured" HONESTLY, and so the production wiring is a data feed away —
    not a redesign. EEO data was confirmed optional for the PoC (00-START-HERE).
    """
    return [
        MonitorResult(
            category=category,
            note="Not computed in the PoC: needs voluntary self-disclosure data "
            "held separately from scoring (production §12).",
        )
        for category in monitoring_categories
    ]
