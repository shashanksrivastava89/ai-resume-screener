"""Bulk-Print splitting: no phantom slices, and an honest shortfall (2026-07-30).

Field report (iCIMS 2026-38973): 333 batch files produced only 309 Review
candidates. The gap was 17 duplicate files + 7 unscorable ones, and both traced to
the same defect — when a TOC name can't be located in pypdf's text,
`_interpolate_starts` guesses, and once the remaining pages run out it CLAMPS
several names onto one page. Every collapsed pair was then written as a
BYTE-IDENTICAL slice: two files carrying one content-hash candidate_id, which
silently merged into a single candidate downstream (so the batch over-reported),
while a neighbouring slice swallowed several résumés and grew to 19 pages (refused
by ingest's single-résumé guard).

Offline throughout — PDFs are built in-test, never real candidate data (§6.12).
"""

from __future__ import annotations

from pathlib import Path

from resume_screener import resume_split
from resume_screener.ats.local_folder import batch_files
from resume_screener.models import candidate_id_for
from tests.test_resume_split import make_pdf


# --- the span mapper -----------------------------------------------------------------


def test_collapsed_names_do_not_become_duplicate_segments():
    """5 TOC names but only 3 résumé pages: interpolation clamps names 3-5 onto the
    last page. Every emitted segment must start on its OWN page — otherwise the
    identical spans become identical PDFs and merge into one candidate."""
    page_texts = [
        "Bulk Print Summary Table of Contents 1. Alice Alpha - Resume "
        "2. Bob Beta - Resume 3. Carol Gamma - Resume 4. Dan Delta - Resume "
        "5. Eve Epsilon - Resume",
        "Alice Alpha alice@example.com",
        "Bob Beta bob@example.com",
        "some trailing page with no recognizable name",
    ]
    names = ["Alice Alpha", "Bob Beta", "Carol Gamma", "Dan Delta", "Eve Epsilon"]
    segments = resume_split._segments_from_toc(page_texts, names, toc_end=0)
    assert segments is not None
    starts = [s.start for s in segments]
    assert starts == sorted(set(starts)), f"segments share a start page: {starts}"
    spans = [(s.start, s.end) for s in segments]
    assert len(spans) == len(set(spans)), f"duplicate page spans: {spans}"
    # page coverage stays contiguous — dropping a collapsed name leaves no gap
    for a, b in zip(segments, segments[1:]):
        assert a.end == b.start - 1


def test_clean_toc_still_maps_every_name():
    """The good path is untouched: when every name anchors, all of them split."""
    page_texts = [
        "Bulk Print Summary Table of Contents 1. Alice Alpha - Resume "
        "2. Bob Beta - Resume 3. Carol Gamma - Resume",
        "Alice Alpha alice@example.com",
        "Bob Beta bob@example.com",
        "Bob Beta continued page two",
        "Carol Gamma carol@example.com",
    ]
    names = ["Alice Alpha", "Bob Beta", "Carol Gamma"]
    segments = resume_split._segments_from_toc(page_texts, names, toc_end=0)
    assert segments is not None
    assert [(s.start, s.end) for s in segments] == [(1, 1), (2, 3), (4, 4)]


# --- the write path ------------------------------------------------------------------


def _degenerate_bulk_pdf() -> bytes:
    """A 5-name TOC whose last three names never appear in the text, and only three
    résumé pages to hold them — the shape that produced the field failure."""
    return make_pdf([
        ["Bulk Print Summary", "Table of Contents",
         "1. Alice Alpha - Resume", "2. Bob Beta - Resume", "3. Carol Gamma - Resume",
         "4. Dan Delta - Resume", "5. Eve Epsilon - Resume"],
        ["Alice Alpha", "Senior Accountant", "alice@example.com"],
        ["Bob Beta", "Analyst", "bob@example.com"],
        ["unlabelled continuation page"],
    ])


def test_no_two_derived_files_share_content(tmp_path):
    """The core guarantee: distinct files in the batch, distinct content hashes —
    so the batch count can never over-report the candidates Review will show."""
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk.pdf").write_bytes(_degenerate_bulk_pdf())

    results = resume_split.split_batch_folder(folder)
    assert len(results) == 1

    files = batch_files(folder)
    hashes = [candidate_id_for(p.read_bytes()) for p in files]
    assert len(hashes) == len(set(hashes)), "identical slices were written as separate files"
    # every derived file recorded is really on disk, and vice versa
    assert sorted(results[0].derived) == sorted(p.name for p in files)


def test_shortfall_is_recorded_against_the_toc_count(tmp_path):
    """What the TOC promised vs what we could separate is reported, not silent."""
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk.pdf").write_bytes(_degenerate_bulk_pdf())

    result = resume_split.split_batch_folder(folder)[0]
    assert result.expected == 5                      # the TOC listed five people
    assert result.count < 5                          # we could not separate them all
    # a shortfall counts only names that produced NOTHING and weren't a duplicate
    assert result.shortfall == 5 - result.count - result.duplicates_dropped
    assert result.shortfall > 0

    entry = resume_split.read_manifest(folder)["bulk.pdf"]
    assert entry["expected"] == 5
    assert entry["shortfall"] == result.shortfall

    groups = resume_split.batch_groups(folder)
    assert groups[0]["expected"] == 5
    assert groups[0]["shortfall"] == result.shortfall


def test_a_clean_bulk_file_reports_no_shortfall(tmp_path):
    """A cleanly-mapped bulk print carries expected == count and no warnings, so
    the Candidates tab stays quiet for the normal case."""
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk.pdf").write_bytes(make_pdf([
        ["Bulk Print Summary", "Table of Contents",
         "1. Alice Alpha - Resume", "2. Bob Beta - Resume"],
        ["Alice Alpha", "alice@example.com"],
        ["Bob Beta", "bob@example.com"],
    ]))
    result = resume_split.split_batch_folder(folder)[0]
    assert (result.expected, result.count, result.shortfall) == (2, 2, 0)
    assert result.duplicates_dropped == 0
    assert result.oversized == []
    group = resume_split.batch_groups(folder)[0]
    assert "shortfall" not in group and "oversized" not in group


def test_an_oversized_slice_is_flagged_for_the_recruiter(tmp_path):
    """A slice too long to be one résumé still holds several candidates. ingest
    refuses to SCORE it (so it never becomes a bogus candidate); the split must
    also name it up front — that is the '19 pages' file from the field report."""
    pages = [["Bulk Print Summary", "Table of Contents",
              "1. Alice Alpha - Resume", "2. Zoe Omega - Resume"]]
    pages.append(["Alice Alpha", "alice@example.com"])
    # 17 pages that never name Zoe -> Alice's span swallows them all
    pages.extend([[f"continuation page {i}"] for i in range(17)])
    pages.append(["Zoe Omega", "zoe@example.com"])

    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk.pdf").write_bytes(make_pdf(pages))

    result = resume_split.split_batch_folder(folder)[0]
    assert result.oversized, "an over-long slice must be reported"
    assert resume_split.batch_groups(folder)[0]["oversized"] == result.oversized

    # and the engine's own guard refuses to score it as a single candidate
    from resume_screener.ingest import MAX_RESUME_PAGES, single_resume_problem
    from pypdf import PdfReader

    flagged = Path(folder) / result.oversized[0]
    pages_in = len(PdfReader(str(flagged)).pages)
    assert pages_in > MAX_RESUME_PAGES
    assert single_resume_problem("", pages_in) is not None
