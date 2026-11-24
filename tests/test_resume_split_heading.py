"""Recovering résumés buried inside an unseparated slice (2026-08-26).

THE REAL CASE. A 47-name pilot packet produced one 19-page slice holding a
candidate plus four neighbours: the UI said "several résumés" and screening
refused it, so those four people were unreachable without manual work. The TOC
scan misses them because it walks names in order with a forward cursor — once a
name fails to match, later occurrences are never looked for.

Two recovery signals, strongest first: the page actually CONTAINS the missing
name (verifiable, so the piece gets that name), else the page opens with text
much larger than its own body text (a cut, but deliberately left unnamed).

Why "much larger than its OWN body text" and not an absolute point size: a Bulk
Print concatenates résumés authored in different tools. Measured on one real
packet, per-page body text ran 1.0, 10.5, 12.0, 15.4 and 327.7 pt and
y-positions ran 25 to 32402, so only a page-relative comparison survives.

Offline: every PDF is built in-test (never real candidate data, §6.12).
"""

from __future__ import annotations

from tests.test_resume_split import _esc

from resume_screener import resume_split


def make_pdf_sized(pages: list[list[tuple[str, int]]]) -> bytes:
    """Like tests.test_resume_split.make_pdf, but each line carries its own point
    size — the only way to exercise a heading signal, which is a RATIO between a
    page's largest opening text and its own body text."""
    objs: list[bytes] = [b"", b""]
    kids: list[int] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    for lines in pages:
        parts = ["BT 72 720 Td"]
        for text, size in lines:
            parts.append(f"/F1 {size} Tf ({_esc(text)}) Tj 0 -{size + 4} Td")
        content = " ".join(parts) + " ET"
        cbytes = content.encode("latin-1")
        content_num = add(b"<</Length %d>>\nstream\n%s\nendstream" % (len(cbytes), cbytes))
        page_num = add(
            b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents %d 0 R /Resources <</Font <</F1 <</Type /Font "
            b"/Subtype /Type1 /BaseFont /Helvetica>> >> >> >>" % content_num)
        kids.append(page_num)

    objs[0] = b"<</Type /Catalog /Pages 2 0 R>>"
    kids_str = " ".join(f"{n} 0 R" for n in kids).encode()
    objs[1] = b"<</Type /Pages /Kids [%s] /Count %d>>" % (kids_str, len(kids))

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<</Size {len(objs) + 1} /Root 1 0 R>>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()
    return bytes(out)


BODY = [("Senior Accountant, GL and R2R across Ind AS and GST", 11),
        ("SAP FICO, Blackline, monthly close and audit support", 11),
        ("Handled reconciliations and statutory filings end to end", 11)]


def _heading_page(name: str, size: int = 26) -> list[tuple[str, int]]:
    return [(name, size), *BODY]


def _plain_page(marker: str) -> list[tuple[str, int]]:
    return [(marker, 11), *BODY]


# --- the signal itself ----------------------------------------------------------------


def test_heading_signal_is_page_relative_not_absolute():
    """A big name over small body text fires; the same size as body text does not —
    even though the absolute point size is identical in both."""
    big = make_pdf_sized([_heading_page("Asha Rao", 26)])
    same = make_pdf_sized([[("Asha Rao", 11), *BODY]])
    assert resume_split._PageText(big).opens_with_a_heading(0) is True
    assert resume_split._PageText(same).opens_with_a_heading(0) is False


def test_heading_signal_ignores_a_long_opening_sentence():
    """A whole sentence set large is a title or a banner, not a person's name."""
    wordy = make_pdf_sized([[("Curriculum Vitae And Complete Professional History Of", 26), *BODY]])
    assert resume_split._PageText(wordy).opens_with_a_heading(0) is False


def test_heading_signal_says_no_on_an_unreadable_page():
    """A scan carries no text spans; no signal is a valid answer, never a crash."""
    assert resume_split._PageText(make_pdf_sized([[]])).opens_with_a_heading(0) is False


# --- recovery inside an oversized slice ------------------------------------------------


TOC_5 = [("Bulk Print Summary", 12), ("Table of Contents", 12),
         ("1. Asha Rao - Resume", 12), ("2. Bela Jain - Resume", 12),
         ("3. Carol Gupta - Resume", 12), ("4. Dev Lillis - Resume", 12),
         ("5. Esha Roach - Resume", 12)]


def _packet_with_buried_people(interior: list[list[tuple[str, int]]]) -> bytes:
    """How the real 19-page slice actually forms: the FIRST few TOC names fail to
    match, and `_interpolate_starts` has nowhere to put a name before the first
    anchor, so it clamps them all onto one page — one span, several résumés.

    Here names 1-3 print spellings the TOC does not match ("B. Jain" vs "Bela
    Jain"); names 4-5 match, giving the two anchors `_MIN_ANCHOR_FRACTION` needs.
    `interior` supplies the pages of the three buried résumés."""
    return make_pdf_sized([TOC_5, *interior,
                           _heading_page("Dev Lillis"), _heading_page("Esha Roach")])


def test_buried_resumes_are_separated_and_left_unnamed(monkeypatch):
    """The people are recovered as their own résumés; because their printed names
    do not match the TOC, the pieces are NOT given those names — a wrong name on a
    candidate's file is the one error that misattributes a real person."""
    # The real slice is oversized; keep the fixture small by lowering the threshold
    # this pass gates on rather than building a 19-page PDF.
    monkeypatch.setattr(resume_split, "_MAX_PAGES_PER_RESUME", 2)
    buried = [_heading_page("A. Rao"), _heading_page("B. Jain"), _heading_page("C. Gupta")]
    segments, method, expected, _ = resume_split._detect_with_method(
        _packet_with_buried_people(buried))

    assert expected == 5
    assert len(segments) == 5, [(s.label, s.start, s.end) for s in segments]
    assert "heading" in method
    assert [s.label for s in segments[-2:]] == ["Dev Lillis", "Esha Roach"]  # matched by name
    assert [s.label for s in segments[1:3]] == [resume_split._UNNAMED_LABEL] * 2
    # Every page still covered exactly once — no gaps, no overlaps.
    assert [(s.start, s.end) for s in segments] == [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)]


def test_a_recovered_piece_gets_its_name_when_the_page_proves_it(monkeypatch):
    """When the buried résumé DOES print the TOC name, the piece is named from that
    evidence rather than left unnamed."""
    monkeypatch.setattr(resume_split, "_MAX_PAGES_PER_RESUME", 2)
    toc = [("Bulk Print Summary", 12), ("Table of Contents", 12),
           ("1. Asha Rao - Resume", 12), ("2. Dev Lillis - Resume", 12)]
    # Dev's own pages name him, but page 1 mentions him too, so the forward-cursor
    # scan anchors both TOC names onto Asha's span and leaves Dev without a page.
    pages = [toc,
             [("Asha Rao", 26), ("Referred by Dev Lillis", 11), *BODY],
             _plain_page("continued"),
             _heading_page("Dev Lillis"),
             _plain_page("continued")]
    segments, method, expected, _ = resume_split._detect_with_method(make_pdf_sized(pages))

    assert expected == 2
    labels = [s.label for s in segments]
    assert "Dev Lillis" in labels, labels
    assert resume_split._UNNAMED_LABEL not in labels


def test_subdivision_never_exceeds_the_missing_count(monkeypatch):
    """The budget is the number of names without a page. A span full of headings
    cannot be shredded into more résumés than the TOC accounts for."""
    monkeypatch.setattr(resume_split, "_MAX_PAGES_PER_RESUME", 1)
    # 5 heading pages, but the TOC names only 2 people and one already has a page.
    segments = resume_split._subdivide_oversized(
        [resume_split.Segment("Asha Rao", 1, 5)],
        ["Asha Rao", "Dev Lillis"],
        resume_split._PageText(make_pdf_sized(
            [[("cover", 11)]] + [_heading_page(f"Person {i}") for i in range(5)])),
    )
    assert len(segments) == 2  # one cut only — a single name was missing
    assert segments[0].label == "Asha Rao"
    assert segments[1].label == resume_split._UNNAMED_LABEL


def test_a_well_separated_packet_is_untouched():
    """No missing names => no subdivision, and the method stays the plain TOC path."""
    from tests.test_resume_split import _bulk_pdf

    segments, method, expected, _ = resume_split._detect_with_method(_bulk_pdf())
    assert [s.label for s in segments] == ["Alice Alpha", "Bob Beta", "Carol Gamma"]
    assert method == "icims_toc" and expected == 3


def test_oversized_slice_with_no_signal_stays_whole_and_is_reported(monkeypatch):
    """When nothing in the span looks like a boundary we do NOT invent one: the
    slice stays oversized and keeps its honest "several résumés" reporting, exactly
    as before this pass existed."""
    monkeypatch.setattr(resume_split, "_MAX_PAGES_PER_RESUME", 2)
    # The three buried résumés carry neither a matching name nor any heading.
    buried = [_plain_page("more"), _plain_page("more"), _plain_page("more")]
    segments, method, expected, _ = resume_split._detect_with_method(
        _packet_with_buried_people(buried))

    assert expected == 5
    assert "heading" not in method                       # nothing was subdivided
    oversized = [s for s in segments
                 if (s.end - s.start + 1) > resume_split._MAX_PAGES_PER_RESUME]
    assert len(oversized) == 1 and oversized[0].start == 1
    assert resume_split._UNNAMED_LABEL not in [s.label for s in segments]
