"""Redaction tests (CLAUDE.md §6.2): identity gone, job content intact. Offline."""

from resume_screener.redact import redact_text

SAMPLE = """GERARD BRANAOLA
Email: contact-9a1ea3@outside.example | Mobile: +91 98765 43210
Address: 12-B, MG Road, Andheri West, Mumbai, PIN: 400058
DOB: 15/06/1987
Gender: Male
Marital Status: Married
Father's Name: Oisin Carrig
Nationality: Indian
PAN No: ABCDE1234F
LinkedIn: https://linkedin.com/in/example-candidate-1987

SUMMARY
Finance professional with 12 years in GST compliance and statutory audit.

EXPERIENCE
Senior Finance Manager, Tata Motors Ltd, Mumbai (Apr 2018 - Present)
- Led Ind AS transition; filed GST/TDS returns; owned SOX controls
- Managed budgets of 7500000 INR across 3 plants
Accountant, Sharma & Associates (2012 - 2018)

EDUCATION
B.Com, University of Delhi, 2009
CA, ICAI, 2012

SKILLS
SAP FICO, Tally, Excel, GST, TDS
"""


def run() -> str:
    return redact_text(SAMPLE, "abc123def456", "GERARD BRANAOLA.pdf").redacted_text


def test_identity_removed():
    out = run().lower()
    assert "rahul" not in out
    assert "branaola" not in out
    assert "gmail" not in out and "@" not in out
    assert "98765" not in out
    assert "400058" not in out  # PIN code
    assert "15/06/1987" not in out  # DOB
    assert "male" not in out.replace("[candidate]", "")
    assert "married" not in out
    assert "oisin" not in out  # father's name (labeled line)
    assert "nationality" not in out and "indian" not in out
    assert "abcde1234f" not in out  # PAN
    assert "linkedin.com/in" not in out


def test_job_content_preserved():
    out = run()
    assert "Tata Motors" in out
    assert "Ind AS" in out and "GST" in out and "TDS" in out and "SOX" in out
    assert "Apr 2018 - Present" in out
    assert "Sharma & Associates" in out  # employer name != candidate name stays
    assert "7500000" in out  # money is not a phone number
    assert "B.Com" in out and "University of Delhi" in out and "2009" in out
    assert "CA, ICAI, 2012" in out
    assert "SAP FICO" in out and "Tally" in out
    assert "12 years in GST compliance" in out  # experience years are not an age


def test_candidate_id_passthrough_and_counts():
    result = redact_text(SAMPLE, "abc123def456", "GERARD BRANAOLA.pdf")
    assert result.candidate_id == "abc123def456"
    assert result.counts.get("name_tokens", 0) >= 2
    assert result.counts.get("labeled_lines", 0) >= 6


# --- dates are evidence, phones are identifiers (2026-09-25) -------------------------
#
# The number rule joined digits, hyphens and ANY whitespace, then masked 10–13 digits.
# A closed job written "06-2016 - 07-2019" is twelve such digits, so on a résumé that
# dates that way only the current "…- Present" job reached the model — and a phone
# glued to a neighbouring year went over the 13-digit cap and through unmasked.
# Synthetic strings only.

import pytest  # noqa: E402

from resume_screener.redact import _mask_number_runs  # noqa: E402

_KEPT = [
    "Finance Manager | 06-2016 - 07-2019",
    "Finance Manager | 2016-06 - 2019-07",
    "Finance Manager | 06/2016 - 07/2019",
    "Finance Manager | 06-2016 – 07-2019",
    "Finance Manager | Jun 2016 - Jul 2019",
    "Finance Manager | 2016 - 2019",
    "Finance Manager | 01.2015 - 12.2018",
    "Finance Manager | 09-2024 - Present",
    "2012 - 2014\n2014 - 2016",              # a column of year ranges, one per line
    "B.Tech, GJUST (2012-2015)",
    "Awards 2012 2013 2014 2015",            # a list of years is not a phone
]

_MASKED = [
    "98765 43210", "+91 98765 43210", "+91-98765-43210", "0124-4567890",
    "(415) 555-2019", "1234 5678 9012", "612 345 678",
]

_GLUED = [  # a phone beside a date or a year — these used to leak whole
    "Working here since 2019\n98765 43210",
    "B.Com 2008-2011 98765 43210",
    "06-2016 - 07-2019 98765 43210",
    "since 2019 98765 43210",
]


@pytest.mark.parametrize("line", _KEPT)
def test_an_employment_date_is_never_masked_as_a_phone(line):
    assert _mask_number_runs(line) == (line, 0)


@pytest.mark.parametrize("line", _MASKED)
def test_every_phone_shape_is_still_masked_whole(line):
    out, n = _mask_number_runs(line)
    assert (out, n) == ("[PHONE]", 1)    # no stray "(" left behind either


@pytest.mark.parametrize("line", _GLUED)
def test_a_phone_beside_a_date_or_a_year_no_longer_slips_through(line):
    out, n = _mask_number_runs(line)
    assert n == 1 and "98765" not in out and "43210" not in out


def test_the_date_beside_the_phone_survives():
    assert _mask_number_runs("06-2016 - 07-2019 98765 43210")[0] == "06-2016 - 07-2019 [PHONE]"


def test_a_backwards_year_pair_is_not_waved_through_as_a_date():
    """Only a range that runs forwards is shielded — a digit pair that merely looks like
    two years stays inside the phone check."""
    out, n = _mask_number_runs("0124-2019-2012")
    assert n == 1 and "2019" not in out


def test_a_full_resume_keeps_its_dates_and_loses_its_phone():
    text = ("Mobile: 98765 43210\n"
            "Finance Manager, Acme | 06-2016 - 07-2019\n"
            "Accountant, Globex | 2014-08 - 2016-05\n")
    out = redact_text(text, "x", "x.pdf").redacted_text
    assert "06-2016 - 07-2019" in out and "2014-08 - 2016-05" in out
    assert "98765" not in out
