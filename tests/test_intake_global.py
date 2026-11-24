"""Intake outside India: phone de-dup and Bulk Print name anchoring (2026-09-12).

Both of these were tuned to the pilot's market without anyone deciding to tune
them, and both fail QUIETLY — one by missing a duplicate, the other by attaching
one person's résumé pages to another person's name. Offline.
"""

from __future__ import annotations

from resume_screener import resume_split
from resume_screener.contacts import tokens_from_text


# --- phones: the same person, written the way their own country writes it ----------


def _phones(text: str) -> set[str]:
    return {t for t in tokens_from_text(text) if t.startswith("phone:")}


def test_a_hungarian_mobile_in_two_spellings_is_one_person():
    """"+36 30 123 4567" and "06 30 123 4567" are the same number. Keeping the
    last TEN digits — right for India's uniform 10-digit plan — made them
    disagree, because the trunk prefix "06" is part of one and not the other."""
    international = _phones("Telefon: +36 30 123 4567")
    national = _phones("Telefon: 06 30 123 4567")
    assert international & national


def test_an_indian_mobile_still_de_dups_the_way_it_always_did():
    assert _phones("+91 98765-43210") & _phones("9876543210")


def test_a_us_number_de_dups_across_its_formats():
    assert _phones("(415) 555-2671") & _phones("+1 415 555 2671")


def test_a_nine_digit_national_mobile_produces_a_token_at_all():
    """Spanish, Czech and Portuguese national mobiles are nine digits. They used
    to fall under the 10-digit floor and yield NOTHING, so those candidates
    de-duped on email alone."""
    assert _phones("Teléfono: 612 345 678")


def test_two_different_people_do_not_collide():
    assert not (_phones("+36 30 123 4567") & _phones("+36 30 765 4321"))


def test_a_year_range_is_not_a_phone_number():
    assert not _phones("Worked there 2018 - 2024 in Budapest")


# --- Bulk Print: whose pages are these? --------------------------------------------


def test_an_accented_name_anchors_to_its_own_page():
    page = "Kovács Péter\nPénzügyi vezető, MOL Nyrt.\n"
    assert resume_split._page_has_name(page, "Kovács Péter")


def test_a_table_of_contents_and_a_page_may_disagree_about_accents():
    """pypdf routinely drops diacritics, so the TOC entry and the extracted page
    can spell the same person differently. Both directions have to match."""
    assert resume_split._page_has_name("Kovacs Peter\nController\n", "Kovács Péter")
    assert resume_split._page_has_name("Kovács Péter\nController\n", "Kovacs Peter")


def test_two_letter_fragments_never_anchor_anyone():
    """The old tokeniser split "Kovács Péter" into ["kov", "cs", "ter"] and
    matched them as bare substrings, so a page of ordinary prose "contained" the
    candidate. That is worse than no match: it attaches one person's résumé to
    another person's name."""
    stranger = ("Interim Controller at Deloitte, focused on statutory reporting "
                "and the accounts payable centre.\n")
    assert not resume_split._page_has_name(stranger, "Kovács Péter")


def test_a_name_part_does_not_match_inside_a_longer_word():
    assert not resume_split._page_has_name("Interim reporting lead\n", "Ter Horst")


def test_the_surname_still_decides_between_two_people_who_share_a_first_name():
    page = "Anna Schröder\nLeiterin Rechnungswesen\n"
    assert resume_split._page_has_name(page, "Anna Schröder")
    assert not resume_split._page_has_name(page, "Anna Weber")
