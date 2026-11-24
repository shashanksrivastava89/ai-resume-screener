"""Length of experience: the one requirement the model may settle by arithmetic.

The guardrail is unchanged — every quote is still verified against the résumé (§6.5).
What is new is that a requirement no SINGLE passage can support ("at least 2 years of
progressive experience") is carried by the dated role lines instead, and the adding up
is done in CODE, so the number is deterministic and replays from the artifact the way
Layer 1 does.
"""
from __future__ import annotations

from resume_screener import findings as F
from resume_screener.findings import FindingDraft, ExperienceSpanDraft, verify_findings
from resume_screener.models import Requirement, Rubric


RESUME = (
    "PROFESSIONAL EXPERIENCE\n"
    "Senior Accounts Payable Analyst, Acme Services — 06/2022 – Present\n"
    "  Owned month-end close for the payables sub-ledger.\n"
    "Accounts Payable Analyst, Globex — 11/2019 – 06/2022\n"
    "  Processed vendor invoices in SAP.\n"
    "Retail Sales Associate — 01/2018 – 10/2019\n"
)


def _rubric(kind: str = "hard_gate") -> Rubric:
    return Rubric(
        role_title="AP Analyst",
        jd_ref="ap.md",
        version=1,
        requirements=[Requirement(id="r1", text="At least 2 years of progressive experience "
                                                "in payables", type="experience", kind=kind,
                                  weight=1.0)],
    ).normalized()


def _verify(draft: FindingDraft, kind: str = "hard_gate"):
    return verify_findings([draft], _rubric(kind), RESUME)[0]


# --- the arithmetic itself --------------------------------------------------------

def test_months_are_summed_inclusively():
    # 2019-11 .. 2022-06 is 32 months counting both ends
    assert F.total_experience_months([(2019 * 12 + 10, 2022 * 12 + 5)]) == 32


def test_concurrent_jobs_are_one_stretch_of_a_working_life():
    """Two roles held at once must not double-count: on a gate that is the difference
    between a pass and a fail."""
    a = (2020 * 12, 2022 * 12 + 11)      # 2020-01 .. 2022-12  = 36
    b = (2021 * 12, 2023 * 12 + 11)      # 2021-01 .. 2023-12  = 36, overlapping
    assert F.total_experience_months([a, b]) == 48   # 2020-01 .. 2023-12, not 72


def test_adjacent_spans_join_without_a_phantom_gap():
    a = (2019 * 12 + 10, 2022 * 12 + 5)
    b = (2022 * 12 + 6, 2024 * 12 + 5)
    assert F.total_experience_months([a, b]) == 32 + 24


def test_a_bare_year_opens_and_closes_its_year():
    assert F._month_index("2020", end=False) == 2020 * 12          # January
    assert F._month_index("2020", end=True) == 2020 * 12 + 11      # December


def test_present_resolves_to_today_and_junk_resolves_to_nothing():
    assert F._month_index("present", end=True) is not None
    assert F._month_index("some time ago", end=True) is None
    assert F._month_index("1823-04", end=True) is None


# --- the guardrail still holds ----------------------------------------------------

def test_dated_lines_carry_a_requirement_no_single_sentence_states():
    """The case that started this (2026-09-07): a candidate whose dated roles plainly
    add up still failed a "2 years" gate, because the résumé nowhere says "2 years"."""
    f = _verify(FindingDraft(
        requirement_id="r1", match="full", evidence=None,
        note="Payables roles run from 11/2019.",
        experience_spans=[
            ExperienceSpanDraft(quote="Senior Accounts Payable Analyst, Acme Services — 06/2022 – Present",
                                start="2022-06", end="present"),
            ExperienceSpanDraft(quote="Accounts Payable Analyst, Globex — 11/2019 – 06/2022",
                                start="2019-11", end="2022-06"),
        ],
    ))
    assert f.match == "full"                      # the gate passes
    assert f.evidence in {s.quote for s in f.experience_spans}
    assert "Downgraded" not in (f.note or "")
    assert f.experience_months is not None and f.experience_months >= 24
    assert "add up to about" in (f.note or "")    # our number, stated plainly


def test_an_invented_dated_line_is_dropped_like_any_other_quote():
    f = _verify(FindingDraft(
        requirement_id="r1", match="full", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote="Chief Payables Officer, Initech — 01/2010 – Present",
                                start="2010-01", end="present"),
        ],
    ))
    assert f.match == "none" and f.evidence is None
    assert f.experience_spans == [] or all(s.quote != "Chief Payables Officer, Initech — 01/2010 – Present"
                                           for s in f.experience_spans)
    assert "Downgraded" in (f.note or "")


def test_the_model_does_not_get_to_assert_the_total():
    """It reads the dates off the page; the sum is ours. A wrong claim in the note
    cannot move the number."""
    f = _verify(FindingDraft(
        requirement_id="r1", match="full", evidence=None,
        note="This adds up to 30 years.",
        experience_spans=[
            ExperienceSpanDraft(quote="Accounts Payable Analyst, Globex — 11/2019 – 06/2022",
                                start="2019-11", end="2022-06"),
        ],
    ))
    assert f.experience_months == 32
    assert "about 2 yr 8 mo" in (f.note or "")


def test_no_dates_and_no_sentence_is_not_found_rather_than_a_guess():
    f = _verify(FindingDraft(requirement_id="r1", match="none", evidence=None,
                             note="No dates printed.", experience_spans=[]))
    assert f.match == "none" and f.experience_months is None


def test_a_stated_length_is_still_the_first_choice():
    """Preference order: a sentence the résumé actually contains wins outright as the
    EVIDENCE (the spans, since p35, come alongside it — see below)."""
    text = RESUME + "\nSummary: 5+ years of experience in accounts payable.\n"
    f = verify_findings([FindingDraft(
        requirement_id="r1", match="full",
        evidence="5+ years of experience in accounts payable", note=None,
    )], _rubric(), text)[0]
    assert f.match == "full"
    assert f.evidence == "5+ years of experience in accounts payable"
    assert f.experience_months is None


def test_a_stated_length_now_comes_WITH_the_working():
    """2026-09-11 (prompt p35): the résumé's own sentence is still the evidence, but
    the dated lines come with it. "6+ years of experience" is the easiest line on a
    résumé to write without it being so, and the reviewer was being shown the answer
    with no way to check it."""
    text = RESUME + "\nSummary: 5+ years of experience in accounts payable.\n"
    f = verify_findings([FindingDraft(
        requirement_id="r1", match="full",
        evidence="5+ years of experience in accounts payable", note=None,
        experience_spans=[
            ExperienceSpanDraft(quote="Senior Accounts Payable Analyst, Acme Services — 06/2022 – Present",
                                start="2022-06", end="present"),
            ExperienceSpanDraft(quote="Accounts Payable Analyst, Globex — 11/2019 – 06/2022",
                                start="2019-11", end="2022-06"),
        ],
    )], _rubric(), text)[0]
    # what the candidate claimed stays the evidence…
    assert f.evidence == "5+ years of experience in accounts payable"
    # …and the working is there to hold it to
    assert len(f.experience_spans) == 2
    assert f.experience_months is not None and f.experience_months >= 24


# --- one position vs a whole working life -----------------------------------------

def test_a_span_is_measured_on_its_own_even_though_the_total_merges():
    """Both numbers are true and neither replaces the other: two concurrent roles are
    36 months each AND 48 months of a working life. The journey shows the first, the
    gate rests on the second."""
    a, b = ("2020-01", "2022-12"), ("2021-01", "2023-12")
    assert F.span_months(*a) == 36 and F.span_months(*b) == 36
    assert F.total_experience_months([F.span_bounds(*a), F.span_bounds(*b)]) == 48


def test_an_undatable_span_measures_to_nothing_rather_than_to_zero():
    assert F.span_months("some time ago", "later") is None
    assert F.span_months("2022-06", "2019-01") is None   # backwards is not a length


def test_span_ongoing_reads_the_ways_a_resume_says_still_there():
    assert F.span_ongoing("Present") and F.span_ongoing("till date")
    assert not F.span_ongoing("2022-06")


def test_spans_are_ignored_on_a_none_so_a_stale_number_never_shows():
    f = _verify(FindingDraft(
        requirement_id="r1", match="none", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote="Retail Sales Associate — 01/2018 – 10/2019",
                                start="2018-01", end="2019-10"),
        ],
    ))
    assert f.experience_spans == [] and f.experience_months is None


def test_a_span_carries_the_job_title_and_only_a_real_one():
    """2026-09-11 (prompt p36). A résumé's employment line is very often
    "<name>, <employer> 06/2024 - Present" — and the name is redacted before the model
    sees the text, so the journey headed a row "[CANDIDATE], McKinsey…" for someone who
    was a Finance Process Lead. The title is now its own field, and it is verified like
    every other quoted string: a title the résumé does not contain is dropped.

    Dropped, not fatal — the span still counts. The dates are what a gate rests on; the
    title is the label that tells a reviewer which job they are looking at, and losing a
    label must never cost someone the evidence of their own experience.
    """
    text = RESUME + "\nFinance Process Lead\n[CANDIDATE], McKinsey — 06/2024 – Present\n"
    f = verify_findings([FindingDraft(
        requirement_id="r1", match="full", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote="[CANDIDATE], McKinsey — 06/2024 – Present",
                                start="2024-06", end="present", title="Finance Process Lead"),
            ExperienceSpanDraft(quote="Accounts Payable Analyst, Globex — 11/2019 – 06/2022",
                                start="2019-11", end="2022-06", title="Vice President of Everything"),
        ],
    )], _rubric(), text)[0]
    assert len(f.experience_spans) == 2, "a refused title must not void the span"
    assert f.experience_spans[0].title == "Finance Process Lead"
    assert f.experience_spans[1].title == "", "a title the résumé never stated is not shown"


def test_a_span_without_a_title_is_still_a_span():
    """Runs screened before p36 carry no title at all."""
    f = verify_findings([FindingDraft(
        requirement_id="r1", match="full", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote="Accounts Payable Analyst, Globex — 11/2019 – 06/2022",
                                start="2019-11", end="2022-06"),
        ],
    )], _rubric(), RESUME)[0]
    assert f.experience_spans[0].title == ""


# --- the count shows its scope (p38, 2026-09-25) ------------------------------------
#
# A "7 years managing AP regional projects" requirement counted two of a ten-year
# résumé's five jobs, and the screen showed only those two — "2 yr 1 mo" read as the
# candidate's whole career. Every dated job now comes back; the ones the requirement is
# not about are listed with counted=False and the model's reason, and the note states
# both totals so a scoped number is never mistaken for a working life.

_SALES = "Retail Sales Associate — 01/2018 – 10/2019"
_SENIOR = "Senior Accounts Payable Analyst, Acme Services — 06/2022 – Present"
_GLOBEX = "Accounts Payable Analyst, Globex — 11/2019 – 06/2022"


def test_every_dated_job_comes_back_and_only_the_counted_ones_add_up():
    f = _verify(FindingDraft(
        requirement_id="r1", match="full", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote=_SENIOR, start="2022-06", end="present"),
            ExperienceSpanDraft(quote=_GLOBEX, start="2019-11", end="2022-06"),
            ExperienceSpanDraft(quote=_SALES, start="2018-01", end="2019-10",
                                counted=False, why_not="sales role, not payables"),
        ],
    ))
    assert [s.counted for s in f.experience_spans] == [True, True, False]
    assert f.experience_spans[2].why_not == "sales role, not payables"
    # the counted total starts at 11/2019; the all-jobs total at 01/2018 — 22 months more
    assert f.experience_months_total - f.experience_months == 22
    assert "The 2 roles counted toward this requirement add up to about" in f.note
    assert "all 3 roles dated on the résumé total about" in f.note


def test_a_left_out_job_can_never_be_the_evidence():
    """Listed to show what the requirement left out — so, by definition, not about it."""
    f = _verify(FindingDraft(
        requirement_id="r1", match="partial", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote=_SALES, start="2018-01", end="2019-10",
                                counted=False, why_not="sales role"),
            ExperienceSpanDraft(quote=_GLOBEX, start="2019-11", end="2022-06"),
        ],
    ))
    assert f.evidence == _GLOBEX


def test_a_listed_job_the_resume_does_not_contain_is_named_not_hidden():
    """A total that silently lost a job is the one a reviewer cannot question."""
    f = _verify(FindingDraft(
        requirement_id="r1", match="full", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote=_GLOBEX, start="2019-11", end="2022-06"),
            ExperienceSpanDraft(quote="Chief Payables Officer, Initech — 01/2010 – 10/2019",
                                start="2010-01", end="2019-10"),
        ],
    ))
    assert f.experience_months == 32 and len(f.experience_spans) == 1
    assert "1 role the AI listed couldn't be found word for word in the résumé" in f.note


def test_a_none_clears_left_out_jobs_too():
    """Nothing counted and nothing quoted is a downgrade to none — and a none shows no
    count, not even the context rows."""
    f = _verify(FindingDraft(
        requirement_id="r1", match="partial", evidence=None, note=None,
        experience_spans=[
            ExperienceSpanDraft(quote=_SALES, start="2018-01", end="2019-10",
                                counted=False, why_not="sales role"),
        ],
    ))
    assert f.match == "none"
    assert f.experience_spans == [] and f.experience_months is None
    assert f.experience_months_total is None


def test_nothing_counted_but_a_stated_length_still_says_what_the_jobs_total():
    f = _verify(FindingDraft(
        requirement_id="r1", match="partial",
        evidence="Accounts Payable Analyst, Globex — 11/2019 – 06/2022", note=None,
        experience_spans=[
            ExperienceSpanDraft(quote=_SALES, start="2018-01", end="2019-10",
                                counted=False, why_not="sales role"),
        ],
    ))
    assert f.experience_months is None and f.experience_months_total == 22
    assert "None of the 1 role dated on the résumé was counted" in f.note


def test_an_older_artifact_reads_every_span_as_counted():
    """Runs screened before p38 held only counted spans and no second total."""
    from resume_screener.models import ExperienceSpan, Finding
    span = ExperienceSpan.model_validate({"quote": _GLOBEX, "start": "2019-11", "end": "2022-06"})
    assert span.counted is True and span.why_not == ""
    old = Finding.model_validate({"requirement_id": "r1", "requirement_text": "x",
                                  "match": "full", "experience_months": 32})
    assert old.experience_months_total is None
