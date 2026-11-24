"""Reading a résumé that is not in English (global rollout, 2026-09-12). Offline.

Three failures, all of which cost a real candidate something:

* A résumé that ends a job with "jelenleg" or "heute" produced an unreadable end
  bound, the span was dropped silently, and the candidate's CURRENT job — the one
  the role is being hired against — contributed zero months. The note still
  reported a confident total. On a length-of-experience hard gate that is a
  rejection with arithmetic behind it.
* "01/2020" and "01-2020" did not parse at all, so a résumé that prints its dates
  month-first lost every span.
* A model retyping "Pénzügyi vezető" as "Penzugyi vezeto" had its quote called
  invented, and the requirement was scored unmet with a note about a quote.

The last one is the delicate change, so its test comes in a pair: an accent
retyped is accepted, and a quote stitched from two passages is still refused. The
guardrail (§6.5) is untouched — only how the SAME characters may be written.
"""

from __future__ import annotations

from datetime import date

from resume_screener.findings import (
    FINDINGS_PROMPT_VERSION,
    _month_index,
    quote_in_text,
    span_months,
    span_ongoing,
)


# --- dates written the way a country writes them -----------------------------------


def test_month_first_dates_are_read():
    """No month is four digits, so a 4-digit year on the right is unambiguous."""
    year_first = _month_index("2020-04", end=False)
    for spelling in ("04/2020", "04-2020", "04.2020"):
        assert _month_index(spelling, end=False) == year_first, spelling


def test_a_hungarian_year_dot_month_is_read():
    assert _month_index("2018. 04.", end=False) == _month_index("2018-04", end=False)


def test_an_impossible_month_is_still_refused():
    assert _month_index("13/2020", end=False) is None
    assert _month_index("not a date", end=False) is None


def test_a_bare_year_still_opens_and_closes_its_own_year():
    assert _month_index("2020", end=False) < _month_index("2020", end=True)


# --- "and I am still there", in the résumé's own words ------------------------------


def test_still_there_is_read_in_the_languages_we_serve():
    for word in ("present", "jelenleg", "napjainkig", "heute", "aktuell",
                 "actualidad", "oggi", "heden", "obecnie", "atualmente",
                 "în prezent", "dosud", "aujourd'hui"):
        assert span_ongoing(word), word
        assert _month_index(word, end=True) is not None, word


def test_an_ongoing_hungarian_role_counts_its_months():
    """The bug in one line: this used to be 0."""
    today = date.today()
    start = f"{today.year - 3}-{today.month:02d}"
    assert span_months(start, "jelenleg") >= 35


def test_the_current_job_is_read_the_same_whatever_the_page_says():
    today = date.today()
    start = f"{today.year - 2}-01"
    assert span_months(start, "jelenleg") == span_months(start, "present")
    assert span_months(start, "heute") == span_months(start, "present")


# --- the evidence rule: accents are spelling, a stitch is not ----------------------


RESUME = ("Pénzügyi vezető, MOL Nyrt., Budapest (2018. 04. – jelenleg)\n"
          "IFRS beszámolók készítése, SAP FICO bevezetés.\n"
          "Könyvelő, Deloitte Zrt. (2012 – 2018)\n")


def test_a_quote_retyped_without_its_accents_is_still_the_resumes_own_words():
    assert quote_in_text(RESUME, "Penzugyi vezeto, MOL Nyrt., Budapest")


def test_the_resumes_exact_spelling_is_accepted_first():
    assert quote_in_text(RESUME, "Pénzügyi vezető, MOL Nyrt., Budapest")


def test_a_stitched_quote_is_still_refused_after_folding():
    """Two real fragments joined into a sentence the résumé never contained. The
    accent fold must not become a way through this."""
    assert not quote_in_text(RESUME, "Pénzügyi vezető, Deloitte Zrt.")
    assert not quote_in_text(RESUME, "Penzugyi vezeto, Deloitte Zrt.")


def test_an_invented_quote_is_still_refused():
    assert not quote_in_text(RESUME, "Vezérigazgató, OTP Bank")


# --- the cache key ------------------------------------------------------------------


def test_the_prompt_version_moved_so_cached_findings_are_re_read():
    assert FINDINGS_PROMPT_VERSION == "p38"
