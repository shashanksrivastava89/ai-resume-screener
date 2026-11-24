"""Governed, jurisdiction-derived blocked patterns (2026-07-21, Phase 1).

The universal fairness floor moved out of prefcheck code into policies/base.yaml
(blocked_patterns) as GOVERNED DATA; overlays add region-specific patterns; and
classify_text resolves the floor per the req's jurisdiction. These tests pin:

  * base-floor PARITY — the migrated set classifies the canonical strings exactly
    as the old hard-coded lexicon did;
  * PER-JURISDICTION — salary history is BLOCKED for California but not India;
  * FLOOR PROTECTION — an overlay may never redefine a base floor pattern;
  * regex/phrase VALIDATION at load; the strictest-UNION view; and the fail-safe
    fallback to the code seed when a policy read yields nothing.
"""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from resume_screener import policy
from resume_screener.models import BlockedPattern
from resume_screener.prefcheck import classify_text

# --- base-floor parity (against the REAL repo base.yaml, repo cwd) ------------------


@pytest.mark.parametrize("text,level", [
    ("prefer male candidates", "blocked"),
    ("only candidates under 35", "blocked"),
    ("must not have a disability", "blocked"),
    ("prefers candidates in Mumbai", "warn"),          # location proxy (warn)
    ("native Hindi speakers preferred", "warn"),        # language proxy (warn)
    ("prefers start-ups", "warn"),                      # employer-age proxy (warn)
    ("strong Python and SQL skills", "ok"),
])
def test_base_floor_parity(text, level):
    # No jurisdiction passed -> the universal base floor (base.yaml, then seed).
    assert classify_text(text).level == level


def test_the_code_seed_mirrors_the_base_floor():
    """The code seed is the fallback when base.yaml can't be read. If the two drift,
    a policy read failure silently changes what is blocked."""
    from resume_screener.prefcheck import _BLOCKED_PATTERNS, _WARN_PATTERNS

    base = {p.id: p for p in policy.base_blocked_patterns()}
    for pid, pat in {**_BLOCKED_PATTERNS, **_WARN_PATTERNS}.items():
        assert base[pid].regexes == [pat.pattern], pid


@pytest.mark.parametrize("text", [
    "prefers unmarried candidates",
    "ethnicity should match the team",
    "females only",
    "women only",
    "only men please",
    "lady candidates preferred",
])
def test_the_widened_floor_blocks_these(text):
    """Pen-test RS-01 (2026-10-16): attribute words the floor did not list."""
    assert classify_text(text).level == "blocked"
    assert classify_text(text, stage="checklist").level == "blocked"


@pytest.mark.parametrize("text", [
    "women's health product knowledge",
    "Women in Finance network member",
    "menswear retail accounts",
    "sportsmen candidates",
    # "religious" is a WARN phrase (scoring_model.v5), not part of the floor: the
    # floor has no approval path, and this is ordinary payroll work (2026-10-16)
    "Familiarity with religious and public holiday calendars for multi-country payroll",
])
def test_the_widened_floor_leaves_these_alone(text):
    assert classify_text(text).level != "blocked"


def test_blocked_reason_comes_from_governed_data():
    v = classify_text("prefer female candidates")
    assert v.level == "blocked"
    assert "protected attribute" in v.reason  # the reason is the governed pack text


# --- per-jurisdiction: California blocks salary history, India does not -------------


def test_salary_history_blocked_in_california_not_india():
    ca = policy.resolve_policy("US-CA").blocked_patterns
    inp = policy.resolve_policy("IN").blocked_patterns
    assert "ca_salary_history" in {p.id for p in ca}
    assert "ca_salary_history" not in {p.id for p in inp}

    assert classify_text("candidate salary history", blocked_patterns=ca).level == "blocked"
    # Not blocked for India (a warn topic or ok — never the California hard block).
    assert classify_text("candidate salary history", blocked_patterns=inp).level != "blocked"


def test_universal_floor_survives_in_every_jurisdiction():
    for code in ("IN", "US-CA", "EU"):
        ids = {p.id for p in policy.resolve_policy(code).blocked_patterns}
        assert {"protected_demographic", "age", "disability", "sexual_orientation"} <= ids


# --- floor protection: an overlay can never redefine a base floor pattern ----------


def _write_pack(dir_, name, body):
    (dir_ / f"{name}.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


def _base_body():
    return """
    jurisdiction: base
    floor:
      redaction: true
      human_decision: true
      evidence_required: true
      excluded_signals: true
      decision_logging: true
    blocked_patterns:
      - id: age
        name: Age
        reason: Preferences can never be based on age.
        severity: blocked
        floor: true
        stages: [preferences, checklist]
        regexes: ['\\bunder\\s*\\d{2}\\b']
    obligations: []
    """


def test_overlay_cannot_redefine_base_floor_pattern(tmp_path):
    policies = tmp_path / "policies"
    policies.mkdir()
    _write_pack(policies, "base", _base_body())
    _write_pack(policies, "XX", """
    jurisdiction: XX
    blocked_patterns:
      - id: age
        name: Age (weakened)
        reason: try to downgrade the floor
        severity: warn
        floor: false
        stages: [preferences]
        regexes: ['\\bunder\\s*\\d{2}\\b']
    """)
    with pytest.raises(policy.PolicyError, match="(?i)floor"):
        policy.resolve_policy("XX", policies_dir=policies)


def test_overlay_adds_region_specific_pattern_under_new_id(tmp_path):
    policies = tmp_path / "policies"
    policies.mkdir()
    _write_pack(policies, "base", _base_body())
    _write_pack(policies, "XX", """
    jurisdiction: XX
    blocked_patterns:
      - id: xx_salary
        name: Salary history (XX)
        reason: XX bars salary history.
        severity: blocked
        floor: false
        stages: [preferences, checklist]
        phrases: [salary history]
    """)
    resolved = policy.resolve_policy("XX", policies_dir=policies).blocked_patterns
    ids = {p.id for p in resolved}
    assert ids == {"age", "xx_salary"}  # base floor kept + region pattern added


# --- model validation: regexes/phrases are checked at load -------------------------


def test_invalid_regex_fails_validation():
    with pytest.raises(ValidationError):
        BlockedPattern(id="x", name="X", reason="r", regexes=["("])  # unbalanced


def test_duplicate_phrases_fail_validation():
    with pytest.raises(ValidationError):
        BlockedPattern(id="x", name="X", reason="r", phrases=["salary history", "Salary History"])


# --- strictest union (pref authoring) + fail-safe fallback -------------------------


def test_union_surfaces_a_pattern_blocked_in_any_jurisdiction():
    ids = {p.id for p in policy.blocked_patterns_union()}
    assert "ca_salary_history" in ids  # blocked in California -> visible in the union
    assert {"protected_demographic", "age"} <= ids


def test_empty_floor_falls_back_to_the_code_seed():
    # An empty list is a resolution failure, never a real "no floor" — the seed
    # (the migrated universal lexicon) must still block a protected attribute.
    assert classify_text("prefer male candidates", blocked_patterns=[]).level == "blocked"
