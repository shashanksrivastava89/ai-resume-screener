"""The scoring guard (ingest.single_resume_problem): refuse to score a file that
isn't a single candidate's résumé, so an un-split iCIMS Bulk Print or a merged
packet can never be scored as one candidate (the '82 for 50 résumés' bug).

Defense in depth BEHIND multi-résumé splitting: even if splitting is off or can't
map the file, this stops a meaningless score from ever being produced.
"""

from __future__ import annotations

from resume_screener import ingest


def test_bulk_print_signature_is_rejected():
    text = "Bulk Print Summary\nTable of Contents\n1. A One - Resume\n2. B Two - Resume\n"
    reason = ingest.single_resume_problem(text, page_count=115)
    assert reason and "Bulk Print" in reason


def test_implausible_page_count_is_rejected():
    reason = ingest.single_resume_problem("ordinary résumé text", page_count=115)
    assert reason and "115 pages" in reason
    # a normal résumé length is fine
    assert ingest.single_resume_problem("ordinary résumé text", page_count=3) is None


def test_many_distinct_emails_is_rejected():
    text = "\n".join(f"candidate{i}@example.com works in finance" for i in range(12))
    reason = ingest.single_resume_problem(text, page_count=4)
    assert reason and "different contact emails" in reason


def test_a_normal_single_resume_passes():
    text = ("Jane Roe\nSenior Accountant\njane.roe@example.com\n+91 90000 00000\n"
            "Record to Report, GL close, SAP, Blackline, GST, TDS.\n"
            "Delhi University, B.Com. References: manager@example.com")  # 2 emails, ok
    assert ingest.single_resume_problem(text, page_count=2) is None


def test_page_count_zero_does_not_trip_the_page_rule():
    # docx fallback / cache paths can report 0 pages — must not be flagged on that basis
    assert ingest.single_resume_problem("Jane Roe jane@example.com SAP GL", page_count=0) is None


def test_review_surfaces_skipped_files_with_plain_reasons():
    """A run's per-file errors become recruiter-facing 'skipped' rows on Review, so a
    rejected file explains itself instead of silently producing no candidate."""
    from resume_screener.api.review import _skipped_files

    meta = {"errors": [
        {"file": "bulk_print_output.pdf", "stage": "validate",
         "error": "NotASingleResumeError: This file is an iCIMS Bulk Print holding "
                  "multiple candidates' résumés. Split it first."},
        {"file": "scan.pdf", "stage": "extract", "error": "HttpResponseError: 400"},
        {"file": "weird.pdf", "stage": "findings", "error": "ValueError: boom"},
    ]}
    skipped = _skipped_files(meta)
    assert skipped[0] == {"file": "bulk_print_output.pdf",
                          "reason": "This file is an iCIMS Bulk Print holding multiple "
                                    "candidates' résumés. Split it first."}
    assert "couldn't be read" in skipped[1]["reason"]  # extract → friendly note
    assert "NotASingleResumeError" not in skipped[0]["reason"]  # class prefix stripped
    assert skipped[2]["reason"] == "This file couldn't be screened."  # other stages generic
    assert _skipped_files({}) == []
