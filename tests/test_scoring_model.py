"""The merged scoring-model artifact: schema invariants, version-file loading,
stamps, diffs (knobs + signals + topics), phrase matching, system warnings, and
the default-rot guard (built-in defaults must equal the committed seed — the
exact rot that hit config.py's 75/50/15 vs config.yaml's 88/60/8)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from resume_screener.models import Adjustment
from resume_screener.scoring_model import (
    KNOB_META,
    ScoringModel,
    ScoringModelError,
    default_scoring_model,
    diff_scoring_models,
    get_path,
    knob_meta,
    latest_scoring_model_path,
    load_scoring_model,
    load_scoring_model_version,
    match_topics,
    merge_extra_exclusions,
    resolved_display,
    scoring_model_stamp,
    scoring_model_versions,
    system_warnings,
)

SEED = Path(__file__).resolve().parents[1] / "signals" / "scoring_model.v1.yaml"


def _seed_data() -> dict:
    return yaml.safe_load(SEED.read_text(encoding="utf-8"))


def _write_version(dir: Path, n: int, **overrides) -> Path:
    data = _seed_data()
    data["version"] = n
    for path, value in overrides.items():
        node = data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    p = dir / f"scoring_model.v{n}.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


# --- the default-rot guard -----------------------------------------------------------


def test_default_equals_committed_seed():
    seed = ScoringModel.model_validate(_seed_data())
    assert default_scoring_model().model_dump(exclude={"version"}) == seed.model_dump(
        exclude={"version"}
    )
    assert default_scoring_model().version == 0


def test_seed_carries_the_merged_registry():
    seed = ScoringModel.model_validate(_seed_data())
    assert len(seed.signals) == 12
    assert {t.id for t in seed.topics} >= {
        "employment_gaps", "unemployment_status", "overqualification_or_age_proxies",
        "commute_or_address", "salary_history", "school_prestige_as_such",
        "culture_fit_vibes", "similarity_to_current_employees", "demographic_language",
    }
    # every seeded topic is a floor protection with phrases + at least one stage
    for t in seed.topics:
        assert t.floor and t.phrases and t.stages


def test_every_knob_path_resolves_on_the_seed():
    seed = ScoringModel.model_validate(_seed_data())
    for path in KNOB_META:
        assert get_path(seed, path) is not None
        assert knob_meta(path)["label"]


# --- schema invariants ---------------------------------------------------------------


def test_band_order_enforced():
    data = _seed_data()
    data["bands"]["possible_at"] = 95  # above strong_at=88
    with pytest.raises(Exception, match="Possible < Strong"):
        ScoringModel.model_validate(data)


def test_match_value_keys_and_order():
    data = _seed_data()
    data["coverage"]["match_value"] = {"full": 1.0, "partial": 0.5}
    with pytest.raises(Exception, match="full, partial and none"):
        ScoringModel.model_validate(data)
    data["coverage"]["match_value"] = {"full": 0.4, "partial": 0.5, "none": 0.0}
    with pytest.raises(Exception, match="none ≤ partial ≤ full"):
        ScoringModel.model_validate(data)


def test_clamp_order_enforced():
    data = _seed_data()
    data["clamp"] = {"min": 100, "max": 100}
    with pytest.raises(Exception, match="floor must sit below"):
        ScoringModel.model_validate(data)


def test_total_cap_must_be_positive():
    data = _seed_data()
    data["adjustments"]["total_cap_points"] = 0
    with pytest.raises(Exception):
        ScoringModel.model_validate(data)


def test_signal_and_topic_ids_must_be_unique():
    data = _seed_data()
    data["topics"].append({"id": data["signals"][0]["id"], "name": "Collision",
                           "reason": "why", "stages": ["scoring"], "phrases": []})
    with pytest.raises(Exception, match="both a signal and a never-scored topic"):
        ScoringModel.model_validate(data)


# --- loading: version glob, cache invalidation, defaults fallback ---------------------


def test_bare_dir_falls_back_to_default(tmp_path):
    sm = load_scoring_model(tmp_path)
    assert sm.version == 0
    assert sm.bands.strong_at == 88
    assert len(sm.signals) == 12  # the seed's signals ride along in the default
    assert scoring_model_stamp(tmp_path) == "v0 (default)"


def test_highest_version_wins(tmp_path):
    _write_version(tmp_path, 1)
    _write_version(tmp_path, 3, **{"bands.strong_at": 85})
    _write_version(tmp_path, 2)
    assert [n for n, _ in scoring_model_versions(tmp_path)] == [1, 2, 3]
    assert latest_scoring_model_path(tmp_path).name == "scoring_model.v3.yaml"
    sm = load_scoring_model(tmp_path)
    assert (sm.version, sm.bands.strong_at) == (3, 85)


def test_new_version_file_invalidates_cache(tmp_path):
    _write_version(tmp_path, 1)
    assert load_scoring_model(tmp_path).version == 1
    _write_version(tmp_path, 2, **{"bands.possible_at": 55})
    sm = load_scoring_model(tmp_path)
    assert (sm.version, sm.bands.possible_at) == (2, 55)


def test_as_of_read(tmp_path):
    _write_version(tmp_path, 1)
    _write_version(tmp_path, 2, **{"bands.strong_at": 85})
    assert load_scoring_model_version(1, tmp_path).bands.strong_at == 88
    assert load_scoring_model_version(2, tmp_path).bands.strong_at == 85
    assert load_scoring_model_version(9, tmp_path) is None


def test_invalid_file_raises_plain_error(tmp_path):
    (tmp_path / "scoring_model.v1.yaml").write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(ScoringModelError, match="not a valid scoring model"):
        load_scoring_model(tmp_path)


def test_stamp_shape(tmp_path):
    _write_version(tmp_path, 4)
    stamp = scoring_model_stamp(tmp_path)
    assert stamp.startswith("v4 (") and len(stamp) == len("v4 (") + 12 + 1


# --- diff: knobs, display, signals, topics ---------------------------------------------


def test_diff_labels_and_values():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    new.bands.strong_at = 85
    new.preferences.review_months = 12
    rows = diff_scoring_models(old, new)
    assert {r["path"] for r in rows} == {"bands.strong_at", "preferences.review_months"}
    strong = next(r for r in rows if r["path"] == "bands.strong_at")
    assert strong == {
        "path": "bands.strong_at", "label": "“Strong match” starts at",
        "from": "88", "to": "85",
    }


def test_diff_covers_signals_and_topics():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    quota = new.signal_by_id("quota_attainment_evidence")
    quota.cap_points = 6
    recency = new.signal_by_id("skill_recency")
    recency.enabled_default = False
    gaps = new.topic_by_id("employment_gaps")
    gaps.phrases = gaps.phrases + ["résumé gap"]
    new.topics = [t for t in new.topics if t.id != "salary_history"]
    rows = {r["path"]: r for r in diff_scoring_models(old, new)}
    assert rows["signals.quota_attainment_evidence.cap_points"]["to"] == "+6"
    assert rows["signals.skill_recency.enabled_default"]["to"].startswith("off")
    assert "phrases" in rows["topics.employment_gaps.phrases"]["to"]
    removed = rows["topics.salary_history"]
    assert removed["from"] == "protected (policy floor)"
    assert removed["to"] == "no longer checked"


def test_diff_new_signal_reads_off_until_switched_on():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    new.signals = new.signals + [old.signals[0].model_copy(update={
        "id": "channel_partner_tenure", "display_name": "Channel partner tenure",
        "enabled_default": False})]
    rows = {r["path"]: r for r in diff_scoring_models(old, new)}
    row = rows["signals.channel_partner_tenure"]
    assert row["label"] == "New signal “Channel partner tenure”"
    assert "off until switched on" in row["to"]


def test_diff_covers_display():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    new.display["total_cap_note"] = "New wording {total_cap_points}."
    rows = diff_scoring_models(old, new)
    assert {r["path"] for r in rows} == {"display.total_cap_note"}


def test_resolved_display_fills_numbers():
    out = resolved_display(default_scoring_model())
    assert out["total_cap_note"] == "All context notes together can move a score by at most 8 points."
    assert out["preference_cap_note"].startswith("Each preference can move a score by at most 2 points")
    assert "6 months" in out["review_note"]


# --- trigger-phrase matching ------------------------------------------------------------


def test_match_topics_is_precision_first():
    topics = default_scoring_model().topics
    hit = match_topics("stability concerns due to an employment gap", topics, "scoring")
    assert [t.id for t in hit] == ["employment_gaps"]
    # normal finance vocabulary never triggers
    assert match_topics("ran a gap analysis on coverage gaps", topics, "scoring") == []
    assert match_topics("owns salary JV postings and addresses reconciliation issues",
                        topics, "scoring") == []
    # stages gate enforcement: demographic_language is scoring+narrative only
    assert match_topics("prefers married candidates", topics, "checklist") == []
    assert [t.id for t in match_topics("prefers married candidates", topics, "scoring")] \
        == ["demographic_language"]


def test_code_screen_reads_the_governed_topics():
    from resume_screener.critic import code_screen

    topics = default_scoring_model().topics
    adj = Adjustment(signal_id="tenure_pattern_vs_stability", points=-2,
                     evidence="took two years off", rationale="career gap hurts stability")
    assert code_screen(adj, topics) == ["employment_gaps"]
    # removing the topic genuinely stops enforcement (the governed list drives it)
    remaining = [t for t in topics if t.id != "employment_gaps"]
    assert code_screen(adj, remaining) == []


def test_merge_extra_exclusions_adds_pack_topics():
    from resume_screener.models import ExcludedSignal

    topics = default_scoring_model().topics
    merged = merge_extra_exclusions(topics, [ExcludedSignal(id="union_membership",
                                                            reason="IN pack addition")])
    added = next(t for t in merged if t.id == "union_membership")
    assert added.stages == ["scoring"] and added.phrases == [] and not added.floor
    # already-known ids are not duplicated
    assert len(merge_extra_exclusions(topics, [ExcludedSignal(id="employment_gaps",
                                                              reason="dup")])) == len(topics)


# --- system warnings (the design's computeWarnings, server-side) ------------------------


def test_system_warnings_flag_the_design_heuristics():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    new.coverage.match_value = {"full": 1.0, "partial": 1.0, "none": 0.1}
    new.bands.strong_at = 62
    new.clamp.max = 120
    new.preferences.per_preference_cap_points = 6
    quota = new.signal_by_id("quota_attainment_evidence")
    quota.cap_points = 9
    warnings = system_warnings(old, new)
    texts = " | ".join(w["text"] for w in warnings)
    assert "couldn't tell them apart" in texts
    assert "inflates for nothing" in texts
    assert "labels will flip on tiny differences" in texts
    assert "outside 0–100" in texts
    assert "Typical caps are 2–5" in texts
    assert "outweigh evidenced signals" in texts
    assert all(w["level"] in ("warn", "red", "info") for w in warnings)


def test_floor_topic_removal_is_a_red_warning():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    new.topics = [t for t in new.topics if t.id != "employment_gaps"]
    warnings = system_warnings(old, new)
    red = [w for w in warnings if w["level"] == "red"]
    assert len(red) == 1
    assert "policy-floor protection" in red[0]["text"]
    assert red[0]["path"] == "topics.employment_gaps"


def test_new_signal_is_an_info_note():
    old = default_scoring_model()
    new = old.model_copy(deep=True)
    new.signals = new.signals + [old.signals[0].model_copy(update={
        "id": "new_factor", "display_name": "New factor", "enabled_default": False})]
    notes = [w for w in system_warnings(old, new) if w["level"] == "info"]
    assert len(notes) == 1 and "starts OFF" in notes[0]["text"]
