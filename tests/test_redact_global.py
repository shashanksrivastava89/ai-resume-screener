"""Redaction outside India (global rollout, 2026-09-12). Offline; synthetic samples.

Redaction is a FLOOR item (CLAUDE.md §6.2): a policy pack cannot turn it down, so
it has to hold for every résumé the app will see — not only the ones the pilot
sent. Two failures this suite exists to keep shut, both of which let identity
through silently:

* "Kovács Péter" was NEVER masked. The name harvest split on ``[A-Za-z]``, so it
  produced "Kov" and "ter", and Python's ``\\b`` is Unicode-aware — a search for
  those never matches inside the real name. The candidate's name went to Azure
  OpenAI in full, blind review showed a named candidate, and the count read 0.
* "Születési dátum:" / "Neme:" / "Családi állapot:" matched nothing, so a
  Hungarian candidate's date of birth, gender and marital status reached the
  scoring model — on an EU résumé, which is exactly the exposure the EU pack
  exists to prevent. A US SSN passed too: nine digits, and the digit-run rule
  masked ten or more.

Each sample is paired: identity gone AND job content intact. The second half is
the one that keeps the first honest — masking everything would pass "identity
gone" and leave nothing to screen.
"""

from __future__ import annotations

from resume_screener.redact import redact_text

# --- Hungary (the next pilot) --------------------------------------------------------

SAMPLE_HU = """Kovács Péter
Születési idő: 1987. 06. 15.
Neme: férfi
Családi állapot: házas
Állampolgárság: magyar
Lakcím: 1117 Budapest, Irinyi József utca 4.
Telefon: +36 30 123 4567
E-mail: contact-56c594@outside.example
TAJ szám: 123 456 789

SZAKMAI TAPASZTALAT
Pénzügyi vezető, MOL Nyrt., Budapest (2018. 04. – jelenleg)
- IFRS beszámolók készítése, SAP FICO bevezetés
- ÁFA-bevallás és statisztikai jelentések
Könyvelő, Deloitte Zrt. (2012 – 2018)

VÉGZETTSÉG
Okleveles könyvvizsgáló, 2012
Budapesti Corvinus Egyetem, 2009

EGYÉB
Jogosítvány: B
Nyelvtudás: angol (felsőfok), német (középfok)
"""


def _hu(filename: str = "Kylie Chaaya CV.pdf") -> str:
    return redact_text(SAMPLE_HU, "hu-1", filename).redacted_text


def test_an_accented_name_is_masked_even_from_an_unaccented_filename():
    """A recruiter saves "Kylie Chaaya.pdf"; the résumé prints "Kovács Péter".
    Both spellings have to reach the same person or the name survives."""
    out = _hu().casefold()
    for fragment in ("kovács", "kylie", "péter", "peter"):
        assert fragment not in out, fragment
    assert "[CANDIDATE]" in _hu()


def test_the_letterhead_name_alone_is_enough():
    """Even with a filename that teaches nothing, the printed name goes."""
    out = _hu("resume.pdf").casefold()
    assert "kovács" not in out and "péter" not in out


def test_hungarian_personal_details_are_removed():
    out = _hu().casefold()
    assert "1987" not in out              # date of birth
    assert "férfi" not in out             # gender
    assert "házas" not in out             # marital status
    assert "magyar" not in out            # nationality
    assert "irinyi" not in out            # street address
    assert "123 456 789" not in out       # national health id
    assert "example.hu" not in out        # email


def test_hungarian_job_content_survives():
    out = _hu()
    for kept in ("MOL Nyrt.", "IFRS", "SAP FICO", "ÁFA-bevallás", "Deloitte",
                 "Okleveles könyvvizsgáló", "2018. 04. – jelenleg", "2012"):
        assert kept in out, kept
    # a driving-licence CLASS is a qualification, not an identifier
    assert "Jogosítvány: B" in out
    # and the language keeps its own accents — nothing is transliterated
    assert "beszámolók" in out


# --- Germany -------------------------------------------------------------------------

SAMPLE_DE = """Anna Schröder
Geburtsdatum: 12.03.1985
Familienstand: verheiratet
Staatsangehörigkeit: deutsch
Anschrift: Werner-von-Siemens-Straße 1, 80333 München
Handy: 0171 2345678

BERUFSERFAHRUNG
Leiterin Rechnungswesen, Siemens AG, München (2017 - heute)
- Monatsabschluss nach HGB und IFRS
- SAP S/4HANA Migration begleitet
Bilanzbuchhalterin, KPMG (2011 - 2017)

QUALIFIKATIONEN
Bilanzbuchhalter (IHK), 2014
Führerschein Klasse B
"""


def _de() -> str:
    return redact_text(SAMPLE_DE, "de-1", "Anna Schroeder Lebenslauf.pdf").redacted_text


def test_german_identity_removed():
    out = _de().casefold()
    assert "schröder" not in out and "schroeder" not in out
    assert "anna" not in out
    assert "1985" not in out          # date of birth
    assert "verheiratet" not in out   # marital status
    assert "deutsch" not in out.replace("deutschland", "")  # nationality
    assert "werner-von-siemens" not in out


def test_german_job_content_survives():
    out = _de()
    for kept in ("Siemens AG", "HGB", "IFRS", "SAP S/4HANA", "KPMG",
                 "Bilanzbuchhalter (IHK)", "Führerschein Klasse B", "2014"):
        assert kept in out, kept


# --- the United States ---------------------------------------------------------------

SAMPLE_US = """Maria Gonzalez
SSN: 123-45-6789
Address: 1420 Oak Street, Concord, ZIP: 94520
Phone: (415) 555-2671
Email: maria.gonzalez@example.com

EXPERIENCE
Controller, Acme Life Sciences, Concord CA (2019 - Present)
- Owned SOX 404 controls and the quarterly close
- Grew managed revenue to $4,250,000 across 3 business units
Senior Accountant, Deloitte (2014 - 2019)

CREDENTIALS
CPA, California Board of Accountancy, 2015
"""


def _us() -> str:
    return redact_text(SAMPLE_US, "us-1", "Maria Gonzalez Resume.pdf").redacted_text


def test_a_social_security_number_never_reaches_the_model():
    """Nine digits, so the digit-run rule (10-13) never saw it. On its own
    labelled line the whole line goes, which is the stronger outcome; the inline
    case below is what the [ID] mask is for."""
    assert "123-45-6789" not in _us()


def test_an_unlabelled_social_security_number_is_masked_in_place():
    text = "SUMMARY\nVerified on file 123-45-6789 during onboarding.\n"
    out = redact_text(text, "x", "x.pdf").redacted_text
    assert "123-45-6789" not in out
    assert "[ID]" in out
    assert "during onboarding" in out


def test_us_identity_removed():
    out = _us().casefold()
    assert "maria" not in out and "gonzalez" not in out
    assert "94520" not in out            # ZIP, labelled
    assert "555-2671" not in out         # phone
    assert "example.com" not in out


def test_us_job_content_survives():
    out = _us()
    for kept in ("Acme Life Sciences", "SOX 404", "$4,250,000", "Deloitte",
                 "CPA, California Board of Accountancy", "2015", "2019 - Present"):
        assert kept in out, kept


# --- the rules that keep short words safe --------------------------------------------


def test_a_label_needs_its_separator_so_ordinary_words_survive():
    """Hungarian "nem" is also the word "no"; German "Alter" sits inside
    "Alternative"; French "genre" appears in "genre de contrat". They are listed
    as labels, and a label is only a label at the start of a line with a
    separator after it."""
    prose = ("Nem volt lehetőség a rendszer cseréjére.\n"
             "Alternative Ansätze zur Konsolidierung geprüft.\n"
             "Analyse du genre de contrat et des clauses.\n")
    out = redact_text(prose, "x", "x.pdf").redacted_text
    assert "Nem volt" in out
    assert "Alternative Ansätze" in out
    assert "genre de contrat" in out


def test_a_nine_digit_quantity_is_not_a_phone_number():
    text = "Managed a portfolio of 750000000 INR across 3 plants.\n"
    out = redact_text(text, "x", "x.pdf").redacted_text
    assert "750000000" in out


def test_a_nine_digit_national_mobile_is_masked():
    text = "Kontakt\nTeléfono móvil 612 345 678 disponible.\n"
    out = redact_text(text, "x", "x.pdf").redacted_text
    assert "612 345 678" not in out


def test_counts_carry_categories_not_values():
    result = redact_text(SAMPLE_HU, "hu-1", "Kylie Chaaya CV.pdf")
    assert result.counts["name_tokens"] >= 2
    assert all(isinstance(v, int) for v in result.counts.values())
