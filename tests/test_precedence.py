"""Model versus pack: which one wins, and why regions cannot conflict (2026-09-12).

"What if scoring rules conflict across regions?" is the question this suite
answers, and the answer is that they cannot: nothing in the scoring model belongs
to a region, so there is no region-versus-region case to resolve. There is only
model-versus-pack, and these are its rules — each pinned here, in the same order
`registry.apply_policy` evaluates them.

| What | Who decides |
|---|---|
| bands, clamp, coverage credit, total adjustment cap, preference cap, confidence | the MODEL, everywhere |
| which signals fire here, their caps, their market parameters | the PACK (replace; it may RAISE a cap) |
| never-scored topics and blocked factors | additive only — a pack may add, never remove |
| a retired signal | the MODEL, absolutely |

Offline, against synthetic packs in a tmp workspace so the committed ones are
never edited to make a test pass.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener.models import EffectivePolicy, ExcludedSignal, SignalOverride
from resume_screener.registry import apply_policy, load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNALS = REPO_ROOT / "signals"


@pytest.fixture()
def registry():
    return load_registry(SIGNALS)


def policy(**overrides) -> EffectivePolicy:
    """An effective policy carrying only what a test is about."""
    return EffectivePolicy(
        jurisdiction="TEST", pack="TEST", floor={},
        signal_overrides=overrides.pop("signals", {}),
        additional_exclusions=overrides.pop("exclusions", []),
        obligations=[], notes=[], keep_at_least_days=None, purge_by_days=None,
        monitoring_categories=[],
        **overrides,
    )


def enabled_map(registry, pol) -> dict:
    return {s.id: s for s in apply_policy(registry, pol)[0]}


# --- caps: replace, not minimum ------------------------------------------------------


def test_a_pack_cap_replaces_the_models_downwards(registry):
    model_cap = next(s.cap_points for s in registry.signals
                     if s.id == "quota_attainment_evidence")
    live = enabled_map(registry, policy(
        signals={"quota_attainment_evidence": SignalOverride(cap_points=1.0)}))
    assert live["quota_attainment_evidence"].cap_points == 1.0 < model_cap


def test_a_pack_cap_replaces_the_models_upwards(registry):
    """NOT min(model, pack). India relies on this for `language_evidence`, and
    clamping to the smaller of the two would quietly change a live score."""
    model_cap = next(s.cap_points for s in registry.signals if s.id == "language_evidence")
    live = enabled_map(registry, policy(
        signals={"language_evidence": SignalOverride(cap_points=model_cap + 2)}))
    assert live["language_evidence"].cap_points == model_cap + 2


def test_a_cap_of_zero_turns_a_signal_off(registry):
    live = enabled_map(registry, policy(
        signals={"skill_recency": SignalOverride(cap_points=0)}))
    assert "skill_recency" not in live


# --- enabled: a pack decides both ways -----------------------------------------------


def test_a_pack_can_switch_on_a_signal_the_model_ships_off(registry):
    assert not next(s.enabled_default for s in registry.signals
                    if s.id == "public_work_relevance")
    live = enabled_map(registry, policy(
        signals={"public_work_relevance": SignalOverride(enabled=True)}))
    assert "public_work_relevance" in live


def test_a_pack_can_switch_off_a_signal_the_model_ships_on(registry):
    live = enabled_map(registry, policy(
        signals={"skill_recency": SignalOverride(enabled=False)}))
    assert "skill_recency" not in live


# --- protections beat the dial -------------------------------------------------------


def test_an_exclusion_beats_the_packs_own_enablement(registry):
    """A pack that excludes a topic AND switches its signal on gets the
    exclusion: the check runs after the override, on purpose."""
    live = enabled_map(registry, policy(
        signals={"skill_recency": SignalOverride(enabled=True, cap_points=5)},
        exclusions=[ExcludedSignal(id="skill_recency", reason="not here")]))
    assert "skill_recency" not in live


def test_a_pack_can_only_add_to_the_never_scored_list(registry):
    _enabled, excluded = apply_policy(registry, policy(
        exclusions=[ExcludedSignal(id="something_local", reason="local rule")]))
    ids = {e.id for e in excluded}
    assert {t.id for t in load_registry(SIGNALS).excluded} <= ids
    assert "something_local" in ids


def test_a_retired_signal_stays_retired_whatever_a_pack_says(registry):
    """Checked before the override is even read."""
    retired = next((s.id for s in registry.signals if s.retired), None)
    if retired is None:
        pytest.skip("no retired signal in the current model")
    live = enabled_map(registry, policy(
        signals={retired: SignalOverride(enabled=True, cap_points=5)}))
    assert retired not in live


def test_a_pack_naming_a_signal_the_model_does_not_have_is_an_error(registry):
    from resume_screener.registry import RegistryError

    with pytest.raises(RegistryError):
        apply_policy(registry, policy(signals={"no_such_signal": SignalOverride(enabled=True)}))


# --- the total cap is the model's alone ----------------------------------------------


def test_raised_per_signal_caps_cannot_push_the_total_past_the_models(registry):
    """A pack can raise what ONE signal may move a score by; it has no field at
    all for how far they may move it TOGETHER. Layer 2 scales the whole set back
    to the model's total, so the model still bounds the outcome."""
    from resume_screener.layer2 import enforce_bounds
    from resume_screener.models import Adjustment
    from resume_screener.scoring_model import load_scoring_model

    total_cap = load_scoring_model(SIGNALS).adjustments.total_cap_points
    generous = {s.id: SignalOverride(cap_points=total_cap) for s in registry.signals[:4]}
    live = enabled_map(registry, policy(signals=generous))

    text = "Delivered the migration and beat the plan."
    adjustments = [Adjustment(signal_id=sid, points=total_cap,
                              evidence=text, rationale="r")
                   for sid in list(live)[:4]]
    kept, _notes = enforce_bounds(adjustments, list(live.values()), text, total_cap)
    assert abs(sum(a.points for a in kept)) <= total_cap + 1e-9


# --- two jurisdictions, one model -----------------------------------------------------


def test_editing_one_jurisdictions_pack_cannot_touch_another(tmp_path, monkeypatch):
    """The structural claim, exercised end to end: change EU's dial on a real
    workspace and India's effective model is byte-identical to what it was."""
    from resume_screener import effective_model, reqcache

    shutil.copytree(REPO_ROOT / "policies", tmp_path / "policies")
    packs = tmp_path / "policies"

    before = effective_model.effective_view("IN", packs, SIGNALS)

    eu = (packs / "EU.yaml").read_text(encoding="utf-8")
    (packs / "EU.yaml").write_text(
        eu.replace("signal_overrides:",
                   "signal_overrides:\n  skill_recency:\n    cap_points: 5"),
        encoding="utf-8")
    reqcache.clear()

    after = effective_model.effective_view("IN", packs, SIGNALS)
    assert after["signals"] == before["signals"]
    assert after["structural"] == before["structural"]

    # …and EU did move
    eu_view = effective_model.effective_view("EU", packs, SIGNALS)
    assert next(s["effective_cap"] for s in eu_view["signals"]
                if s["id"] == "skill_recency") == 5
