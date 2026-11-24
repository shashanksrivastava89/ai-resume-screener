"""P39: the deterministic fairness classifier (prefcheck.classify_text).

Precision-first: protected attributes are BLOCKED, job-relevance proxies WARN,
everything else OK — and finance vocabulary that merely *looks* sensitive
("gap analysis", "salary processing") must stay OK. Severest level wins; pack
additional_exclusions passed via extra_exclusions escalate to WARN. Fully offline.
"""

from __future__ import annotations

import pytest

from resume_screener.prefcheck import classify_text


# --- BLOCKED: protected attributes -----------------------------------------------------


@pytest.mark.parametrize("text", [
    "prefers candidates under 30",
    "avoid candidates from a particular caste",
    "no recent pregnancy please",
    "prefers male candidates",
    "someone of the same religion",
    "no disabled candidates",
    "prefers heterosexual candidates",
    "candidates with a 2020 graduation year",
    "must state date of birth",
    "prefers younger candidates",
])
def test_blocked_protected_attributes(text):
    assert classify_text(text).level == "blocked"


# --- WARN: job-relevance proxies -------------------------------------------------------


@pytest.mark.parametrize("text", [
    "prefers candidates from tier-1 colleges",
    "prefers candidates in Mumbai",
    "prefers candidates with no career gaps",
    "wants to see current salary",
    "prefers candidates from big startups",
    "someone who is a good culture fit",
    "similar to our current team",
    "local candidates only",
])
def test_warn_proxies(text):
    assert classify_text(text).level == "warn"


# --- WARN: the stand-ins the pen test used (2026-10-16, RS-01) ------------------------
# Governed phrases in scoring_model.v5 (family_and_caregiving, and career breaks under
# employment_gaps). WARN, not blocked: the same words appear in ordinary HR work
# ("administering maternity leave policies"), so a person decides. The intent screen
# escalates real exclusion intent; these hold even when it can't run.


@pytest.mark.parametrize("text", [
    "no primary caregiver responsibilities",
    "candidates who have not had extended career breaks",
    "no sabbaticals in the last five years",
    "no maternity leave in the next year",
    "candidates without young children",
    "no family commitments that limit travel",
    "sin compromisos familiares significativos",
    "shaadi shuda nahi",
    "single parents need not apply",
])
def test_warn_family_and_career_break_stand_ins(text):
    v = classify_text(text)
    assert v.level == "warn", text
    assert classify_text(text, stage="checklist").level == "warn", text


def test_religious_observance_waits_for_a_person():
    v = classify_text("candidates not engaged in religious observance")
    assert v.level == "warn" and "demographic_language" in v.basis_ids


def test_ordinary_hr_work_with_those_words_waits_rather_than_stops():
    v = classify_text("experience administering maternity leave policies", stage="checklist")
    assert v.level == "warn" and v.basis_ids == ["family_and_caregiving"]


# --- OK: genuine, job-related preferences + finance negatives --------------------------


@pytest.mark.parametrize("text", [
    "prefers candidates with US GAAP experience",
    "strong treasury and audit background",
    "prefers a CA or CPA qualification",
    "experience with Ind AS and GST filings",
    # finance vocabulary that must NOT trip the proxy lexicons:
    "experience with gap analysis and variance reporting",
    "hands-on salary processing and payroll",
    "addresses reconciliation issues quickly",
])
def test_ok_including_finance_negatives(text):
    assert classify_text(text).level == "ok"


# --- extra_exclusions + severest-wins --------------------------------------------------


def test_extra_exclusions_escalate_ok_to_warn():
    text = "prefers deep fintech domain experience"
    assert classify_text(text).level == "ok"
    check = classify_text(text, extra_exclusions=["fintech"])
    assert check.level == "warn" and "pack_exclusion" in check.basis_ids


def test_severest_wins_blocked_over_warn():
    # a protected attribute AND a proxy in one line → blocked (never downgraded)
    check = classify_text("prefers young candidates from tier-1 colleges")
    assert check.level == "blocked"


def test_extra_exclusions_never_downgrade_blocked():
    check = classify_text("prefers candidates under 30", extra_exclusions=["treasury"])
    assert check.level == "blocked"


def test_reason_is_plain_language_and_present():
    assert classify_text("prefers male candidates").reason
    assert classify_text("prefers candidates in Mumbai").reason
    assert classify_text("strong US GAAP experience").reason


def test_empty_text_is_ok():
    assert classify_text("").level == "ok"
    assert classify_text("   ").level == "ok"


# --- spelling does not get past the floor (2026-10-16, pen-test RS-01) -----------------
#
# The pen test got blocked words past the check by writing them differently: a
# zero-width space inside the word, digits for letters, letters spaced apart. Each
# case below is written with explicit escapes so the hidden characters are visible.


@pytest.mark.parametrize("text", [
    "no preg​nancy-related leave please",       # zero-width space inside the word
    "date of birth after 1990",             # no-break spaces
    "pr3gn@ncy-related leave must not apply",          # digits and symbols for letters
    "m4le candidates only",
    "g3nder balance in the team",
    "fema1e candidates",                               # 1 read as l
    "no d1sab1l1ty please",                            # 1 read as i
    "p r e g n a n t candidates excluded",             # spaced-out letters
    "mаle candidates",                            # Cyrillic a
    "ｇｅｎｄｅｒ balance",   # fullwidth letters
    "ſexual orientation",                         # the long s
    "caſte background",
    "Mále candidates",                           # a combining accent
    "pre­gnant",                                  # a soft hyphen
])
def test_obfuscations_are_caught(text):
    assert classify_text(text).level == "blocked", text


@pytest.mark.parametrize("text, level", [
    # the finance vocabulary the folds must not move (levels as they were before)
    ("tier-1 college graduates", "warn"),
    ("SOX 404 controls testing", "ok"),
    ("GST and TDS compliance", "ok"),
    ("Ind AS 116 lease accounting", "ok"),
    ("B.Com or M.Com graduate", "ok"),
    ("audit experience since 2019", "ok"),
    ("Q4 FY24 close", "ok"),
    ("ISO 27001 awareness", "ok"),
    ("L1/L2 support for finance systems", "ok"),
    ("3rd-party vendor reconciliations", "ok"),
    ("1st-line review of journals", "ok"),
    ("SAP S/4HANA migration", "ok"),
    ("24x7 month-end support", "ok"),
    ("P&L ownership", "ok"),
    ("M&A due diligence", "ok"),
    ("CA Inter or CMA Inter", "ok"),
    ("Win32 tooling", "ok"),
    ("email: x@y.com reporting", "ok"),
    ("prefers a CPA from the U.S.A", "ok"),
])
def test_the_folds_leave_ordinary_finance_text_alone(text, level):
    assert classify_text(text).level == level, text


def test_the_text_as_typed_is_checked_first():
    from resume_screener.prefcheck import text_variants

    variants = text_variants("Pr3gnancy cover")
    assert variants[0] == "Pr3gnancy cover"
    assert "pregnancy cover" in variants


def test_a_one_letter_word_before_spaced_letters_stays_a_word():
    from resume_screener.prefcheck import text_variants

    variants = text_variants("start a f a m i l y")
    assert "start a family" in variants and "start afamily" in variants
