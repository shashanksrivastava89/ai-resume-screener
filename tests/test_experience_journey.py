"""The drawer draws the dated positions as a journey — the dates are read HERE.

`review._span_view` orders the spans and measures each one, so the picture and the
total can never drift apart: a résumé date is parsed in exactly one place in this
codebase (findings), and the client only formats what it is handed (2026-09-11).
"""
from __future__ import annotations

from resume_screener.api.review import _span_view


def test_spans_come_back_newest_first_each_with_its_own_length():
    """Reverse chronology (2026-09-11, pilot): a résumé is written newest-first and read
    that way, and the current job is the one being hired against — it was at the bottom
    of the journey."""
    rows = _span_view([
        {"quote": "earlier", "start": "2019-11", "end": "2022-06"},
        {"quote": "current", "start": "2022-06", "end": "present"},
    ])
    assert [r["quote"] for r in rows] == ["current", "earlier"]
    assert rows[0]["ongoing"] is True
    assert rows[1]["months"] == 32                                 # 11/2019–06/2022, both ends
    assert rows[1]["ongoing"] is False


def test_each_row_carries_the_job_title_it_was_held_under():
    """The quoted line is very often "<name>, <employer> 06/2024 - Present" — and the
    name is redacted before the model sees it, so the row used to be headed
    "[CANDIDATE], McKinsey…" for a Finance Process Lead."""
    rows = _span_view([
        {"quote": "[CANDIDATE], McKinsey 06/2024 - Present", "title": "Finance Process Lead",
         "start": "2024-06", "end": "present"},
    ])
    assert rows[0]["title"] == "Finance Process Lead"


def test_a_span_from_before_titles_were_asked_for_reports_an_empty_one():
    """Runs screened under p35 have no title in the artifact. The row still draws — it
    just has no label — rather than the view blowing up on a missing key."""
    rows = _span_view([{"quote": "old", "start": "2019-11", "end": "2022-06"}])
    assert rows[0]["title"] == ""


def test_a_line_we_cannot_date_keeps_its_place_at_the_end_rather_than_vanishing():
    """The quote was verified against the résumé, so it is worth showing; we just
    cannot put it on a timeline."""
    rows = _span_view([
        {"quote": "undated", "start": "some time ago", "end": "later"},
        {"quote": "dated", "start": "2019-11", "end": "2022-06"},
    ])
    assert [r["quote"] for r in rows] == ["dated", "undated"]
    assert rows[1]["months"] is None


def test_the_newest_row_is_first_even_when_the_resume_listed_them_forwards():
    rows = _span_view([
        {"quote": "oldest", "start": "2015-01", "end": "2018-01"},
        {"quote": "middle", "start": "2018-02", "end": "2021-03"},
        {"quote": "newest", "start": "2021-04", "end": "present"},
    ])
    assert [r["quote"] for r in rows] == ["newest", "middle", "oldest"]


def test_two_lines_starting_the_same_month_keep_the_order_the_model_gave():
    rows = _span_view([
        {"quote": "first", "start": "2021-01", "end": "2021-06"},
        {"quote": "second", "start": "2021-01", "end": "2022-06"},
    ])
    assert [r["quote"] for r in rows] == ["first", "second"]


def test_no_spans_is_an_empty_list_not_a_row_of_blanks():
    assert _span_view([]) == []


def test_a_left_out_job_carries_its_flag_and_reason_to_the_drawer():
    rows = _span_view([
        {"quote": "Analyst, Globex — 11/2019 – 06/2022", "start": "2019-11", "end": "2022-06"},
        {"quote": "Sales, Initech — 01/2018 – 10/2019", "start": "2018-01", "end": "2019-10",
         "counted": False, "why_not": "sales role, not payables"},
    ])
    assert [(r["counted"], r["why_not"]) for r in rows] == [
        (True, ""), (False, "sales role, not payables")]


def test_a_span_from_before_p38_is_a_counted_one():
    [row] = _span_view([{"quote": "Analyst, Globex", "start": "2019-11", "end": "2022-06"}])
    assert row["counted"] is True and row["why_not"] == ""
