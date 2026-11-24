"""Signal-registry tests after the 2026-07-18 merge: the registry view derives
from the governed scoring model (seed validity, dup ids, unknown fields, bad
caps now schema-checked on the MODEL), and apply_policy keeps its effective-
policy semantics. All offline."""

from pathlib import Path

import pytest
import yaml

from resume_screener.models import EffectivePolicy, SignalOverride
from resume_screener.registry import RegistryError, apply_policy, load_registry
from resume_screener.scoring_model import ScoringModel

SEED = "signals"  # the registry lives inside signals/scoring_model.v1.yaml

EXPECTED_SIGNAL_IDS = {
    "tenure_pattern_vs_stability", "preferred_domain_background",
    "regulated_environment_exposure", "skill_recency", "scope_trajectory",
    "hands_on_currency", "domain_transfer_distance", "certification_currency",
    "language_evidence", "quota_attainment_evidence", "deal_profile_fit",
    "public_work_relevance",
}
EXPECTED_EXCLUDED_IDS = {
    "employment_gaps", "unemployment_status", "overqualification_or_age_proxies",
    "commute_or_address", "salary_history", "school_prestige_as_such",
    "culture_fit_vibes", "similarity_to_current_employees", "demographic_language",
    "family_and_caregiving",  # scoring_model.v5 (2026-10-16, pen-test RS-01)
}


def _policy(**kwargs) -> EffectivePolicy:
    defaults = dict(
        jurisdiction="XX", pack="XX",
        floor={k: True for k in ("redaction", "human_decision", "evidence_required",
                                 "excluded_signals", "decision_logging")},
        signal_overrides={}, additional_exclusions=[], obligations=[],
        keep_at_least_days=None, purge_by_days=None, monitoring_categories=[],
    )
    defaults.update(kwargs)
    return EffectivePolicy(**defaults)


def _write(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(
        Path("signals/scoring_model.v1.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "scoring_model.v1.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return tmp_path


def test_seed_registry_is_valid_and_complete():
    registry = load_registry(SEED)
    assert {s.id for s in registry.signals} == EXPECTED_SIGNAL_IDS
    assert {e.id for e in registry.excluded} == EXPECTED_EXCLUDED_IDS
    public = next(s for s in registry.signals if s.id == "public_work_relevance")
    assert public.enabled_default is False
    quota = next(s for s in registry.signals if s.id == "quota_attainment_evidence")
    assert quota.cap_points == 5 and quota.direction == "plus"
    deal = next(s for s in registry.signals if s.id == "deal_profile_fit")
    assert deal.cap_points == 4 and deal.direction == "both"
    assert all(s.job_relatedness.strip() for s in registry.signals)
    assert all(s.display_name for s in registry.signals)  # friendly names seeded
    assert all(e.reason.strip() for e in registry.excluded)


def test_duplicate_signal_id_rejected(tmp_path):
    p = _write(tmp_path, lambda r: r["signals"].append(dict(r["signals"][0])))
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(p)


def test_unknown_field_rejected_with_clear_error(tmp_path):
    def mutate(r):
        r["signals"][0]["colour"] = "red"
    p = _write(tmp_path, mutate)
    with pytest.raises(RegistryError, match="colour"):
        load_registry(p)


def test_nonpositive_cap_rejected(tmp_path):
    def mutate(r):
        r["signals"][0]["cap_points"] = 0
    p = _write(tmp_path, mutate)
    with pytest.raises(RegistryError, match="cap_points"):
        load_registry(p)


def test_signal_also_a_topic_rejected(tmp_path):
    def mutate(r):
        r["topics"].append({"id": r["signals"][0]["id"], "name": "Oops",
                            "reason": "oops", "stages": ["scoring"], "phrases": []})
    p = _write(tmp_path, mutate)
    with pytest.raises(RegistryError, match="both a signal and a never-scored topic"):
        load_registry(p)


def test_apply_policy_defaults():
    registry = load_registry(SEED)
    enabled, excluded = apply_policy(registry, _policy())
    ids = {s.id for s in enabled}
    assert "public_work_relevance" not in ids  # enabled_default: false
    assert len(ids) == len(EXPECTED_SIGNAL_IDS) - 1
    assert {e.id for e in excluded} == EXPECTED_EXCLUDED_IDS


def test_apply_policy_override_enable_disable_cap():
    registry = load_registry(SEED)
    policy = _policy(signal_overrides={
        "public_work_relevance": SignalOverride(enabled=True),
        "deal_profile_fit": SignalOverride(enabled=False),
        "language_evidence": SignalOverride(cap_points=3, params={"x": 1}),
    })
    enabled, _ = apply_policy(registry, policy)
    ids = {s.id for s in enabled}
    assert "public_work_relevance" in ids and "deal_profile_fit" not in ids
    lang = next(s for s in enabled if s.id == "language_evidence")
    assert lang.cap_points == 3 and lang.params == {"x": 1}


def test_apply_policy_unknown_override_rejected():
    registry = load_registry(SEED)
    with pytest.raises(RegistryError, match="unknown signal"):
        apply_policy(registry, _policy(signal_overrides={"nope": SignalOverride(enabled=True)}))


def test_policy_additional_exclusion_disables_signal():
    from resume_screener.models import ExcludedSignal

    registry = load_registry(SEED)
    policy = _policy(additional_exclusions=[
        ExcludedSignal(id="language_evidence", reason="jurisdiction says no")
    ])
    enabled, excluded = apply_policy(registry, policy)
    assert "language_evidence" not in {s.id for s in enabled}
    assert "language_evidence" in {e.id for e in excluded}


def test_topics_project_reasons_into_the_excluded_view():
    raw = yaml.safe_load(
        Path("signals/scoring_model.v1.yaml").read_text(encoding="utf-8"))
    model = ScoringModel.model_validate(raw)
    registry = load_registry(SEED)
    reasons = {e.id: e.reason for e in registry.excluded}
    for topic in model.topics:
        assert reasons[topic.id] == topic.reason
