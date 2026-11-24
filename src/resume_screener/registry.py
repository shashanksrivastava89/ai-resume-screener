"""Signal registry: the §4 SignalRegistry view over the governed scoring model.

Since the 2026-07-18 design-led merge, the AI-signal definitions and the
never-scored topics LIVE inside ``signals/scoring_model.v{N}.yaml`` (one
versioned, second-leader-approved artifact) — ``signals/registry.yaml`` is
retired. This module keeps its historical API so the engine is untouched:
``load_registry`` still returns a ``SignalRegistry`` and ``apply_policy`` still
produces the effective (enabled, excluded) view under a jurisdiction pack.
"""

from __future__ import annotations

from pathlib import Path

from resume_screener.models import (
    EffectivePolicy,
    EnabledSignal,
    ExcludedSignal,
    SignalRegistry,
)


class RegistryError(RuntimeError):
    """Registry problem with a message safe and clear enough for the console/UI."""


def load_registry(path: str | Path = "signals") -> SignalRegistry:
    """The signal registry, derived from the current scoring model. `path` is
    the historical signals-file anchor (``signals/registry.yaml``) or the
    signals directory — either way the governed model in that directory is the
    source of truth, so every caller's signature is unchanged."""
    from resume_screener.scoring_model import ScoringModelError, load_scoring_model

    p = Path(path)
    directory = p if p.is_dir() or not p.suffix else p.parent
    if str(directory) in ("", "."):
        directory = Path("signals")
    try:
        sm = load_scoring_model(directory)
    except ScoringModelError as exc:
        raise RegistryError(str(exc)) from exc
    return SignalRegistry(
        version=sm.version,
        signals=list(sm.signals),
        excluded=[ExcludedSignal(id=t.id, reason=t.reason) for t in sm.topics],
    )


def apply_policy(
    registry: SignalRegistry, policy: EffectivePolicy
) -> tuple[list[EnabledSignal], list[ExcludedSignal]]:
    """Effective-policy view of the registry: (enabled signals, full excluded list)."""
    known_ids = {s.id for s in registry.signals}
    unknown = sorted(set(policy.signal_overrides) - known_ids)
    if unknown:
        raise RegistryError(
            f"Policy pack '{policy.pack}' overrides unknown signal(s): {', '.join(unknown)} "
            "— fix the pack or add the signal on the Scoring model page."
        )

    excluded: dict[str, ExcludedSignal] = {e.id: e for e in registry.excluded}
    for extra in policy.additional_exclusions:
        excluded.setdefault(extra.id, extra)

    enabled: list[EnabledSignal] = []
    for signal in registry.signals:
        if signal.retired:  # soft-retired rules are kept in the file but never applied (P25)
            continue
        override = policy.signal_overrides.get(signal.id)
        is_enabled = signal.enabled_default
        if override is not None and override.enabled is not None:
            is_enabled = override.enabled
        if signal.id in excluded:  # a policy exclusion always wins over enablement
            is_enabled = False
        cap = signal.cap_points
        if override is not None and override.cap_points is not None:
            cap = override.cap_points
        if not is_enabled or cap <= 0:  # cap 0 in a pack = signal neutered = disabled
            continue
        data = signal.model_dump()
        data["cap_points"] = cap
        enabled.append(
            EnabledSignal(**data, params=(override.params or {}) if override else {})
        )
    return enabled, list(excluded.values())
