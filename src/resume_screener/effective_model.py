"""The scoring model as it actually applies in one jurisdiction (2026-09-12).

WHY THIS EXISTS. There is ONE scoring model for the whole organisation
(`signals/scoring_model.vN.yaml`). Regional variation is expressed entirely
through the jurisdiction policy packs, which decide WHICH signals fire, their caps
and their market parameters. Nothing anywhere showed that: the Scoring page
presented the one global model as "India (IN)" with a region selector that snapped
back, and told approvers a scoring-model change "applies to India (IN)
requisitions only — other regions keep their own model", which was never true.

So this answers, for any jurisdiction, the question a policy maker actually has:
*what does the model do HERE?*

HOW IT STAYS TRUE. Every per-signal row is derived from
``registry.apply_policy(load_registry(), resolve_policy(j))`` — the SAME call a
screening run makes — and the critic additions from the same two functions
``cli`` composes at run time. This module does not re-implement the precedence
rules; it reads their output and labels where each value came from. A test pins
the equality both ways, because a view that drifts from the run is worse than no
view at all.

PRECEDENCE, for the record (implemented in registry.apply_policy, described here):

* The MODEL owns everything structural — bands, clamp, coverage credit, the total
  adjustment cap, the preference cap, confidence, batch quality. Identical in
  every region; a pack has no field that can move them.
* The PACK decides `enabled` (both directions — it can switch on a signal the
  model ships off, and switch off one it ships on) and `cap_points` (a REPLACE,
  not a minimum: a pack may raise a cap above the model's, and India does exactly
  that for `language_evidence`).
* Exclusions are one-way: a pack may ADD never-scored topics and blocked factors,
  never remove one, and an exclusion beats the pack's own enablement.
* `retired` in the model beats everything, including a pack override.

Two jurisdictions therefore cannot conflict with each other — nothing in the model
belongs to a region, so the only question is model-versus-pack.
"""

from __future__ import annotations

from pathlib import Path

from resume_screener import cascade, reqcache
from resume_screener.models import EffectivePolicy, PolicyPack, SignalRegistry
from resume_screener.policy import (
    _all_overlays,
    _load_base_pack,
    base_blocked_patterns,
    resolve_policy,
)
from resume_screener.registry import apply_policy, load_registry
from resume_screener.scoring_model import (
    ScoringModel,
    load_scoring_model,
    merge_extra_exclusions,
    scoring_model_stamp,
)


def _structural(sm: ScoringModel) -> dict:
    """The parts of the model no policy pack can touch. Flagged as global on the
    wire so a client never has to know which fields those are."""
    return {
        "scope": "global",
        "bands": sm.bands.model_dump(),
        "clamp": sm.clamp.model_dump(),
        "coverage": sm.coverage.model_dump(),
        "adjustments": sm.adjustments.model_dump(),
        "preferences": sm.preferences.model_dump(),
        "confidence": sm.confidence.model_dump(),
        "batch_quality": sm.batch_quality.model_dump(),
    }


def _param_origins(signal_id: str, params: dict, base: PolicyPack | None,
                   pack_name: str) -> dict[str, str]:
    """Which pack set each market parameter: the base defaults, or this overlay.

    Params deep-merge (base ⊕ overlay per key), so "short stint = 9 months" in
    India and "12" in the base are both live values of the same knob and a reader
    should be able to tell which one they are looking at."""
    base_params: dict = {}
    if base is not None:
        override = (base.signal_overrides or {}).get(signal_id)
        base_params = dict((override.params or {}) if override else {})
    return {key: ("base" if base_params.get(key) == value else pack_name)
            for key, value in (params or {}).items()}


def build_effective_view(sm: ScoringModel, registry: SignalRegistry,
                         policy: EffectivePolicy | None,
                         base: PolicyPack | None) -> dict:
    """Pure builder — the whole payload from artifacts already loaded.

    `policy=None` is the GLOBAL view: the model's own defaults, with no pack
    applied. Split out from `effective_view` so it can be tested without a
    filesystem."""
    enabled_by_id = {}
    excluded: list = []
    if policy is not None:
        enabled, excluded = apply_policy(registry, policy)
        enabled_by_id = {s.id: s for s in enabled}

    overrides = (policy.signal_overrides if policy else {}) or {}
    model_topic_ids = {t.id for t in sm.topics}
    excluded_ids = {e.id for e in excluded}
    pack_name = policy.pack if policy else "model"

    signals: list[dict] = []
    for signal in sm.signals:
        override = overrides.get(signal.id)
        live = enabled_by_id.get(signal.id)
        enabled_here = bool(live) if policy is not None else (
            signal.enabled_default and not signal.retired)
        effective_cap = live.cap_points if live else None

        # Where each value came from, and — when the signal does not fire here —
        # why, in the words a reader can act on.
        cap_origin = "pack" if (override and override.cap_points is not None) else "model"
        enabled_origin = "model"
        disabled_reason = None
        if signal.retired:
            enabled_origin, disabled_reason = "retired", "retired from the model"
        elif signal.id in excluded_ids and signal.id not in model_topic_ids:
            enabled_origin = "exclusion"
            disabled_reason = f"excluded by the {pack_name} pack"
        elif signal.id in excluded_ids:
            enabled_origin = "exclusion"
            disabled_reason = "on the never-scored list"
        elif override and override.cap_points == 0:
            enabled_origin, disabled_reason = "cap_zero", f"cap set to 0 by the {pack_name} pack"
        elif override and override.enabled is False:
            enabled_origin, disabled_reason = "pack", f"turned off by the {pack_name} pack"
        elif not enabled_here:
            disabled_reason = "off in the global model"
        elif override and override.enabled is True and not signal.enabled_default:
            enabled_origin = "pack"

        params = dict(live.params) if live else {}
        signals.append({
            "id": signal.id,
            "display_name": signal.display_name or signal.id,
            "description": signal.description,
            "kind": signal.kind,
            "direction": signal.direction,
            "retired": signal.retired,
            "model_cap": signal.cap_points,
            "effective_cap": effective_cap,
            "enabled_in_model": signal.enabled_default,
            "enabled_here": enabled_here,
            "origin": "pack" if (cap_origin == "pack" or enabled_origin == "pack") else "model",
            "enabled_origin": enabled_origin,
            "cap_origin": cap_origin,
            # A pack may RAISE a cap above the model's. Allowed on purpose — India
            # does it — and worth marking, because it is the one direction a
            # reader would not assume.
            "cap_raised": bool(effective_cap is not None
                               and effective_cap > signal.cap_points),
            "disabled_reason": disabled_reason,
            "params": params,
            "param_origins": _param_origins(signal.id, params, base, pack_name),
        })

    # What the critic will never let a score rest on, here. Composed exactly as
    # cli.py composes it for a run.
    pack_exclusions = [
        {"id": e.id, "reason": e.reason}
        for e in (policy.additional_exclusions if policy else [])
    ]
    critic_topics = merge_extra_exclusions(
        sm.topics, policy.additional_exclusions if policy else [])
    jurisdiction_topics = cascade.blocked_patterns_as_topics(
        policy.blocked_patterns if policy else [])
    screened_ids = {t.id for t in jurisdiction_topics}

    # What this jurisdiction adds is what the BASE pack does not already carry —
    # NOT what is unflagged as floor. California's salary-history rule is
    # `floor: true` (Labor Code §432.3 is not advisory there), so filtering on the
    # flag hid the one region-specific rule the US-CA pack exists for.
    base_pattern_ids = {p.id for p in (base.blocked_patterns if base else [])}
    pack_patterns = []
    for pattern in (policy.blocked_patterns if policy else []):
        if pattern.id in base_pattern_ids:
            continue  # the universal floor, reported separately
        pack_patterns.append({
            "id": pattern.id,
            "name": pattern.name,
            "reason": pattern.reason,
            "severity": pattern.severity,
            "stages": list(pattern.stages),
            "phrase_count": len(pattern.phrases or []),
            # Only a scoring/narrative-staged pattern WITH literal phrases reaches
            # the critic's deterministic screen. The rest are checked when someone
            # writes a preference or a checklist row, which is a different moment
            # and worth not implying.
            "critic_screened": f"jurisdiction:{pattern.id}" in screened_ids,
        })

    return {
        "scope": "jurisdiction" if policy else "global",
        "jurisdiction": policy.jurisdiction if policy else None,
        "pack": policy.pack if policy else None,
        "notes": list(policy.notes or []) if policy else [],
        "model": {"version": sm.version},
        "pack_version": None,
        "structural": _structural(sm),
        "signals": signals,
        "critic": {
            "universal_floor": [
                {"id": p.id, "name": p.name, "reason": p.reason,
                 "severity": p.severity, "stages": list(p.stages)}
                for p in (base.blocked_patterns if base else []) if p.floor
            ],
            "model_topics": [
                {"id": t.id, "name": t.name, "reason": t.reason,
                 "floor": t.floor, "stages": list(t.stages)}
                for t in sm.topics
            ],
            "pack_exclusions": pack_exclusions,
            "pack_blocked_patterns": pack_patterns,
            "critic_added_topics": [
                {"id": t.id, "name": t.name, "stages": list(t.stages)}
                for t in jurisdiction_topics
            ],
            "total_never_scored": len(critic_topics) + len(jurisdiction_topics),
        },
    }


@reqcache.memo(key=lambda jurisdiction=None, policies_dir="policies", scoring_dir="signals":
               (jurisdiction or "", str(policies_dir), str(scoring_dir)))
def effective_view(jurisdiction: str | None = None,
                   policies_dir: str | Path = "policies",
                   scoring_dir: str | Path = "signals") -> dict:
    """The model as it applies in `jurisdiction`, or globally when it is None.

    An unknown jurisdiction raises PolicyError from resolve_policy — no fallback,
    for the same reason creating a requisition there is refused: a guess about
    which law applies is not a convenience."""
    sm = load_scoring_model(scoring_dir)
    registry = load_registry(scoring_dir)
    base = _load_base_pack(policies_dir)
    policy = resolve_policy(jurisdiction, policies_dir) if jurisdiction else None
    view = build_effective_view(sm, registry, policy, base)
    view["model"]["stamp"] = scoring_model_stamp(scoring_dir)
    if policy is not None:
        pack = next((p for p in _all_overlays(policies_dir)
                     if p.jurisdiction == policy.pack), None)
        view["pack_version"] = pack.version if pack else None
    if not view["critic"]["universal_floor"]:
        # base.yaml is loaded through a different path under the DB backend; the
        # floor is the same list either way.
        view["critic"]["universal_floor"] = [
            {"id": p.id, "name": p.name, "reason": p.reason,
             "severity": p.severity, "stages": list(p.stages)}
            for p in base_blocked_patterns(policies_dir) if p.floor
        ]
    return view
