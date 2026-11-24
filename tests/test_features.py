"""Feature tests (P7): pure math, policy-params wiring, and the no-gaps floor
guarantee. Everything offline with a fixed as_of date — determinism is the point."""

from datetime import date

from resume_screener.features import (
    FeatureSet,
    certification_currency,
    compute_features,
    must_have_recency,
    tenure_stats,
    trajectory,
)
from resume_screener.models import (
    EffectivePolicy,
    Requirement,
    ResumeProfile,
    RoleStint,
    Rubric,
    SignalOverride,
)

AS_OF = date(2026, 7, 1)  # every test pins the clock — features must be replayable

PROFILE = ResumeProfile(
    candidate_id="cid1",
    source_file="x.pdf",
    skills=["US GAAP", "Tally"],
    roles=[
        # current permanent role since 2021-07 (~60 months at AS_OF)
        RoleStint(title="Senior Treasury Accountant", summary="US GAAP reporting and SOX evidence",
                  start="2021-07", end=None),
        # contract stint — must be EXCLUDED from all tenure math
        RoleStint(title="Consultant", summary="GST filings via Tally ERP",
                  start="2020-07", end="2021-06", employment_type="contract"),
        # completed 18-month permanent stint (2019-01 .. 2020-06)
        RoleStint(title="Accountant", summary="Journal entries and Tally ledgers",
                  start="2019-01", end="2020-06"),
    ],
    education=["B.Com"],
    certifications=["CA, ICAI, 2012", "SAP FI Certified"],
    redacted_text="...",
)

RUBRIC = Rubric(
    role_title="Role", jd_ref="jd.md", version=1,
    requirements=[
        Requirement(id="r01", text="US GAAP reporting", kind="must_have", weight=1),
        Requirement(id="r02", text="Tally ERP ledgers", kind="must_have", weight=1),
        Requirement(id="r03", text="Quantum cryptography research", kind="must_have", weight=1),
        Requirement(id="r04", text="Nice thing", kind="nice_to_have", weight=1),
    ],
)


def _policy(**param_sets) -> EffectivePolicy:
    """EffectivePolicy with given per-signal params, e.g. tenure={'short_stint_months': 24}."""
    overrides = {
        {"tenure": "tenure_pattern_vs_stability",
         "recency": "skill_recency",
         "certs": "certification_currency"}[name]: SignalOverride(params=params)
        for name, params in param_sets.items()
    }
    return EffectivePolicy(
        jurisdiction="XX", pack="XX",
        floor={k: True for k in ("redaction", "human_decision", "evidence_required",
                                 "excluded_signals", "decision_logging")},
        signal_overrides=overrides, additional_exclusions=[], obligations=[],
        keep_at_least_days=None, purge_by_days=None, monitoring_categories=[],
    )


# --- tenure: policy params must actually change the numbers (wiring proof) ---------


def test_tenure_counts_and_exclusions():
    stats = tenure_stats(PROFILE, {"short_stint_months": 12}, AS_OF)
    assert stats.permanent_stints == 2  # unlabeled roles count as permanent
    assert stats.excluded_stints == 1  # the contract stint
    assert stats.completed_stints == 1
    assert stats.avg_completed_months == 17.0  # 2019-01 -> 2020-06
    assert stats.current_stint_months == 60.0  # 2021-07 -> 2026-07
    assert stats.short_stints_in_window == 0  # 17mo is not short at threshold 12


def test_tenure_short_stint_threshold_comes_from_policy():
    stats = tenure_stats(PROFILE, {"short_stint_months": 24}, AS_OF)
    assert stats.short_stints_in_window == 1  # same data, JP-ish threshold -> short
    assert stats.short_stint_pattern is False  # 1 short stint < min_stints_for_pattern (2)
    stats2 = tenure_stats(PROFILE, {"short_stint_months": 24, "min_stints_for_pattern": 1}, AS_OF)
    assert stats2.short_stint_pattern is True


def test_tenure_lookback_window_comes_from_policy():
    # Stint ended 2020-06; a 3-year lookback from 2026-07 starts 2023-07 -> outside.
    stats = tenure_stats(PROFILE, {"short_stint_months": 24, "lookback_years": 3}, AS_OF)
    assert stats.short_stints_in_window == 0


# --- must-have recency ---------------------------------------------------------------


def test_must_have_recency_matching():
    results, fraction = must_have_recency(PROFILE, RUBRIC, {"recent_years": 3}, AS_OF)
    by_id = {r.requirement_id: r for r in results}
    assert set(by_id) == {"r01", "r02", "r03"}  # must-haves only, r04 ignored
    assert by_id["r01"].months_since_last_use == 0.0  # exercised in the current role
    # Tally: matched in contract (ended 2021-06) and old permanent (ended 2020-06);
    # recency takes the MOST RECENT use: 2021-06 -> 2026-07 = 61 months.
    assert by_id["r02"].months_since_last_use == 61.0
    assert by_id["r03"].matched_in_history is False
    assert by_id["r03"].months_since_last_use is None
    # recent within 36 months: only r01 -> 1/3 of ALL must-haves
    assert fraction == round(1 / 3, 3)


def test_recency_window_comes_from_policy():
    _, fraction = must_have_recency(PROFILE, RUBRIC, {"recent_years": 6}, AS_OF)
    assert fraction == round(2 / 3, 3)  # 72-month window now also catches Tally


# --- trajectory & certifications ------------------------------------------------------


def test_trajectory_growth():
    stats = trajectory(PROFILE)
    assert stats.first_level == 2  # Accountant (2019)
    assert stats.last_level == 3  # Senior ... (2021, "senior" outranks "accountant")
    assert stats.delta == 1
    assert stats.dated_roles_considered == 3


def test_trajectory_single_role_is_unknown_not_penalty():
    single = ResumeProfile(candidate_id="c", source_file="s", redacted_text="t",
                           roles=[RoleStint(title="Accountant", start="2024-01")])
    assert trajectory(single).delta is None


def test_certification_staleness_from_policy():
    stats = certification_currency(PROFILE, {"stale_after_years": 5}, AS_OF)
    assert (stats.total, stats.dated_current, stats.dated_stale, stats.undated) == (2, 0, 1, 1)
    fresh = certification_currency(PROFILE, {"stale_after_years": 20}, AS_OF)
    assert fresh.dated_current == 1  # 2012 is "current" with a 20-year policy window


# --- assembly + the floor guarantee ----------------------------------------------------


def test_compute_features_assembles_and_records_params():
    features = compute_features(
        PROFILE, RUBRIC,
        _policy(tenure={"short_stint_months": 9, "lookback_years": 8},
                recency={"recent_years": 3}, certs={"stale_after_years": 5}),
        as_of=AS_OF, domain_fit_cosine=0.81,
    )
    assert features.as_of == "2026-07-01"
    assert features.params_used["tenure.short_stint_months"] == 9
    assert features.params_used["jurisdiction"] == "XX"
    assert features.domain_fit_cosine == 0.81
    assert features.tenure.excluded_stints == 1


def test_no_gap_shaped_field_ever_exists():
    """Floor guarantee (§6): employment gaps are computed NOWHERE. If someone adds
    a gap feature, this fails and points them at the policy floor."""
    schema = str(FeatureSet.model_json_schema()).lower()
    assert "gap" not in schema
    assert "unemploy" not in schema
