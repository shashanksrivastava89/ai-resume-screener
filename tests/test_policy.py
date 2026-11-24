"""Policy engine tests: floor protection (the load-bearing guarantee), merge
semantics, US fallback, and IN-vs-EU divergence. All offline."""

from pathlib import Path

import pytest
import yaml

from resume_screener.policy import FLOOR_ITEMS, PolicyError, resolve_policy
from resume_screener.registry import apply_policy, load_registry
from tests.packdata import pack_days

ALL_PACKS = ["IN", "SG", "JP", "EU", "US-default", "US-NYC", "US-CA"]


def _tmp_policies(tmp_path: Path, overlay: dict) -> Path:
    (tmp_path / "base.yaml").write_text(
        Path("policies/base.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "XX.yaml").write_text(yaml.safe_dump(overlay), encoding="utf-8")
    return tmp_path


def test_every_seed_pack_resolves_with_full_floor():
    for code in ALL_PACKS:
        policy = resolve_policy(code)
        assert policy.pack == code
        for item in FLOOR_ITEMS:
            assert policy.floor.get(item) is True, f"{code} lost floor item {item}"


def test_overlay_cannot_disable_floor(tmp_path):
    overlay = {"jurisdiction": "XX", "floor": {"redaction": False}}
    with pytest.raises(PolicyError, match="floor"):
        resolve_policy("XX", _tmp_policies(tmp_path, overlay))


def test_overlay_cannot_disable_exclusions_floor(tmp_path):
    overlay = {"jurisdiction": "XX", "floor": {"excluded_signals": False}}
    with pytest.raises(PolicyError, match="never be turned off"):
        resolve_policy("XX", _tmp_policies(tmp_path, overlay))


def test_overlay_cannot_invent_a_disable_mechanism(tmp_path):
    overlay = {"jurisdiction": "XX", "remove_exclusions": ["employment_gaps"]}
    with pytest.raises(PolicyError, match="remove_exclusions"):
        resolve_policy("XX", _tmp_policies(tmp_path, overlay))


def test_base_missing_floor_item_fails(tmp_path):
    base = yaml.safe_load(Path("policies/base.yaml").read_text(encoding="utf-8"))
    del base["floor"]["redaction"]
    (tmp_path / "base.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    (tmp_path / "XX.yaml").write_text(yaml.safe_dump({"jurisdiction": "XX"}), encoding="utf-8")
    with pytest.raises(PolicyError, match="redaction"):
        resolve_policy("XX", tmp_path)


def test_in_merge_params_obligations_retention():
    policy = resolve_policy("IN")
    tenure = policy.signal_overrides["tenure_pattern_vs_stability"].params
    assert tenure["short_stint_months"] == 9  # overlay wins
    assert tenure["lookback_years"] == 8  # overlay wins
    assert tenure["min_stints_for_pattern"] == 2  # base key survives the deep merge
    assert policy.signal_overrides["skill_recency"].params["recent_years"] == 3
    assert policy.signal_overrides["language_evidence"].cap_points == 3
    assert any("human reviewer" in o.lower() for o in policy.obligations)  # base
    assert any("dpdp" in o.lower() for o in policy.obligations)  # overlay
    # The two retention duties come from the IN overlay, whatever the pack currently
    # says (it is governed data — see tests/packdata.py); the merge must carry them.
    keep, purge = pack_days("IN")
    assert (policy.keep_at_least_days, policy.purge_by_days) == (keep, purge)
    assert keep and purge and keep > purge  # audit record outlives the candidate PII
    assert policy.monitoring_categories, "IN monitors voluntary gender/disability"
    assert not any("caste" in c.lower() for c in policy.monitoring_categories)


def test_jp_tenure_norms_differ_from_in():
    jp = resolve_policy("JP").signal_overrides["tenure_pattern_vs_stability"].params
    in_ = resolve_policy("IN").signal_overrides["tenure_pattern_vs_stability"].params
    assert jp["short_stint_months"] > in_["short_stint_months"]


def test_us_state_falls_back_to_us_default():
    policy = resolve_policy("US-TX")
    assert policy.jurisdiction == "US-TX"
    assert policy.pack == "US-default"
    assert any("US-default" in n for n in policy.notes)


def test_unknown_jurisdiction_is_an_error_not_a_silent_default():
    with pytest.raises(PolicyError, match="No policy pack"):
        resolve_policy("ZZ")
    with pytest.raises(PolicyError):
        resolve_policy("base")


def test_in_vs_eu_enabled_signals_and_obligations_differ():
    registry = load_registry("signals/registry.yaml")
    enabled_in = {s.id for s in apply_policy(registry, resolve_policy("IN"))[0]}
    enabled_eu = {s.id for s in apply_policy(registry, resolve_policy("EU"))[0]}
    assert "language_evidence" in enabled_in
    assert "language_evidence" not in enabled_eu
    obligations_in = set(resolve_policy("IN").obligations)
    obligations_eu = set(resolve_policy("EU").obligations)
    assert obligations_in != obligations_eu
