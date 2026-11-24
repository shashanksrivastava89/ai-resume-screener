"""Contact de-dup must not swallow whole Bulk Print packets (2026-07-30).

Field report: 8 iCIMS Bulk Print files were imported to a fresh requisition and
only 6 arrived (245 résumés instead of 333). Cause: contact de-dup asks "is this
the same PERSON?" by intersecting the email/phone tokens found in a file — but a
Bulk Print holds 40+ people, so ONE candidate shared with an already-added packet
rejected the ENTIRE file (~40 résumés). Worse, which files died depended on upload
order, so it was nondeterministic.

Rule: a multi-candidate packet is de-duped by CONTENT HASH only; per-person
de-dup happens on the one-candidate slices the splitter produces.

Offline throughout — PDFs are built in-test, never real candidate data (§6.12).
"""

from __future__ import annotations

from resume_screener import resume_split
from resume_screener.ats.local_folder import add_bytes_to_batch, batch_files
from resume_screener.contacts import (
    MAX_SINGLE_RESUME_EMAILS,
    contact_tokens,
    holds_many_candidates,
    tokens_for_path,
)
from tests.test_resume_split import make_pdf


def _resume_pdf(name: str, email: str, phone: str = "9876500000") -> bytes:
    return make_pdf([[name, "Senior Accountant", email, phone, "GL and R2R"]])


def _bulk_pdf(people: list[tuple[str, str, str]]) -> bytes:
    """A Bulk Print: TOC page listing everyone, then one page per person.
    Each person carries their OWN email AND phone, so only a genuinely shared
    person creates an overlap between two packets."""
    toc = ["Bulk Print Summary", "Table of Contents"]
    toc += [f"{i + 1}. {n} - Resume" for i, (n, _, _) in enumerate(people)]
    pages = [toc]
    for name, mail, phone in people:
        pages.append([name, "Accountant", mail, phone])
    return make_pdf(pages)


# 8 people -> 8 emails, above the single-résumé ceiling. Names carry NO digits:
# the TOC entry pattern excludes them ("N. <name> - Resume").
_LETTERS = "ABCDEFGH"
PEOPLE_A = [(f"Alpha{c} Person", f"alpha{i}@example.com", f"98111000{i:02d}")
            for i, c in enumerate(_LETTERS)]
# shares exactly ONE person (the first of A, same email AND phone) — the field scenario
PEOPLE_B = [PEOPLE_A[0]] + [(f"Beta{c} Person", f"beta{i}@example.com", f"98222000{i:02d}")
                            for i, c in enumerate(_LETTERS[:7])]


# --- the classifier -------------------------------------------------------------------


def test_a_single_resume_is_not_multi_candidate():
    tokens = contact_tokens(_resume_pdf("Anita Rao", "anita@example.com"), ".pdf")
    assert tokens and not holds_many_candidates(tokens)


def test_a_bulk_print_is_multi_candidate():
    tokens = contact_tokens(_bulk_pdf(PEOPLE_A), ".pdf")
    assert sum(1 for t in tokens if t.startswith("email:")) > MAX_SINGLE_RESUME_EMAILS
    assert holds_many_candidates(tokens)


# --- the regression: whole packets were rejected --------------------------------------


def test_two_bulk_files_sharing_a_candidate_both_land(tmp_path):
    """The reported bug. Both packets must be accepted — the shared candidate is
    resolved later, per person, not by discarding 40 résumés."""
    batch = tmp_path / "b"
    assert add_bytes_to_batch("bulk_a.pdf", _bulk_pdf(PEOPLE_A), batch) == "added"
    assert add_bytes_to_batch("bulk_b.pdf", _bulk_pdf(PEOPLE_B), batch) == "added"
    assert {p.name for p in batch_files(batch)} == {"bulk_a.pdf", "bulk_b.pdf"}


def test_bulk_files_land_in_any_upload_order(tmp_path):
    """Which files survived used to depend on processing order — pin that out."""
    for order in ([("a", PEOPLE_A), ("b", PEOPLE_B)], [("b", PEOPLE_B), ("a", PEOPLE_A)]):
        batch = tmp_path / f"order_{order[0][0]}"
        outcomes = [add_bytes_to_batch(f"bulk_{tag}.pdf", _bulk_pdf(people), batch)
                    for tag, people in order]
        assert outcomes == ["added", "added"], f"order {[t for t, _ in order]} lost a file"


def test_an_identical_bulk_file_is_still_a_duplicate(tmp_path):
    """Content-hash de-dup still applies to packets — the same export re-uploaded
    must not double the batch."""
    batch = tmp_path / "b"
    blob = _bulk_pdf(PEOPLE_A)
    assert add_bytes_to_batch("bulk.pdf", blob, batch) == "added"
    assert add_bytes_to_batch("bulk_again.pdf", blob, batch) == "duplicate"


# --- the feature still works for real single résumés ----------------------------------


def test_same_person_two_single_resumes_still_dedupes(tmp_path):
    batch = tmp_path / "b"
    assert add_bytes_to_batch("v1.pdf", _resume_pdf("Anita Rao", "anita@example.com"), batch) == "added"
    assert add_bytes_to_batch("v2.pdf", _resume_pdf("Anita R", "anita@example.com"), batch) == "duplicate"


def test_namesakes_with_different_contacts_both_land(tmp_path):
    batch = tmp_path / "b"
    assert add_bytes_to_batch("r1.pdf", _resume_pdf("Rahul Sharma", "rahul.s@example.com", "9811111111"), batch) == "added"
    assert add_bytes_to_batch("r2.pdf", _resume_pdf("Rahul Sharma", "rahul.k@example.com", "9822222222"), batch) == "added"


# --- per-person de-dup now happens on the SLICES --------------------------------------


def test_a_candidate_in_two_bulk_prints_becomes_one_derived_resume(tmp_path):
    """Where the de-dup belongs: after splitting, the person present in both
    packets is kept once, so Review shows them a single time."""
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk_a.pdf").write_bytes(_bulk_pdf(PEOPLE_A))
    resume_split.split_batch_folder(folder)
    first = {p.name for p in batch_files(folder)}
    assert len(first) == len(PEOPLE_A)

    (folder / "bulk_b.pdf").write_bytes(_bulk_pdf(PEOPLE_B))
    resume_split.split_batch_folder(folder)
    total = batch_files(folder)
    # 8 + 8 people, one shared -> 15 distinct candidates, not 16
    assert len(total) == 15, [p.name for p in total]


# --- the token cache ------------------------------------------------------------------


def test_tokens_for_path_caches_and_reacts_to_edits(tmp_path, monkeypatch):
    """_batch_contacts reads every file in the batch on every add; without a cache
    a 300-file batch re-extracted 300 PDFs per upload."""
    path = tmp_path / "r.pdf"
    path.write_bytes(_resume_pdf("Anita Rao", "anita@example.com"))
    first = tokens_for_path(path)
    assert "email:anita@example.com" in first

    calls = {"n": 0}
    real = contact_tokens

    def counting(data, suffix):
        calls["n"] += 1
        return real(data, suffix)

    monkeypatch.setattr("resume_screener.contacts.contact_tokens", counting)
    assert tokens_for_path(path) == first
    assert calls["n"] == 0, "a cache hit must not re-extract"

    # an edited file (new size/mtime) is re-read
    path.write_bytes(_resume_pdf("Bob Beta", "bob@example.com"))
    assert "email:bob@example.com" in tokens_for_path(path)
    assert calls["n"] == 1
