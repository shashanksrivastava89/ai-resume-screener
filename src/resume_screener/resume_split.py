"""Multi-résumé file splitting — TEMPORARY iCIMS bridge (built to be removed).

iCIMS "Bulk Print" downloads pack many candidates' résumés into one PDF. Until the
native iCIMS integration (§12) delivers one file per candidate, this splits such a
file into individual résumés at batch time, so each candidate becomes a normal
one-file-one-candidate entry the rest of the engine already understands (its own
content-hash `candidate_id`, artifacts, private-map entry, Review card). Nothing
downstream changes.

Scope + safety
--------------
- **PDF only.** A multi-résumé DOCX is left as a single candidate (no fixed pages
  to slice on) — the UI notes that.
- **Deterministic primary path.** An iCIMS Bulk Print starts with a "Bulk Print
  Summary → Table of Contents" listing ``N. <Name> - Resume``; each résumé then
  follows in that order, starting on a fresh page. We parse the TOC (exact count +
  ordered names) and locate each résumé by name — no guessing.
- **Conservative default.** No TOC signature ⇒ the file is treated as a single
  résumé and passes through untouched (a plain single-résumé upload is never split).
  This is why the feature can't break the existing flow.
- **LLM safety net.** Only when a TOC IS present but name-matching can't map every
  candidate do we ask the model to assign start pages (lazy, non-fatal; skipped
  offline). A general non-TOC multi-résumé detector is a future extension.
- **Removable.** Delete this module, the ``multi_resume_split`` flag, the
  ``split_batch_folder`` calls in the batch routes, and the ``groups`` field in
  get_batch to remove the feature.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock
from resume_screener.models import candidate_id_for

log = logging.getLogger(__name__)

MANIFEST_NAME = ".split_manifest.json"  # hidden -> batch_files() skips it
SOURCES_DIRNAME = "_sources"            # a subdir -> batch_files() skips it (non-recursive)

# A TOC entry: "1. Aditya Boylan - Resume" (the dash may be -, – or —). Matched with
# finditer (not per-line ^…$) so it survives pypdf extraction that runs entries
# together or wraps them: the name stops at the next digit/newline, so consecutive
# entries on one extracted line still separate cleanly.
_TOC_ENTRY_RE = re.compile(r"(\d{1,3})\.\s+([^\d\n]{2,60}?)\s*[-–—]\s*Resume\b", re.I)
_TOC_SIGNATURE_RE = re.compile(r"bulk print|table of contents", re.I)
_MAX_TOC_PAGES = 4  # the TOC lives on the first pages; don't scan the whole doc


@dataclass
class Segment:
    """One résumé's page span within a bulk file (page indices are 0-based, inclusive)."""
    label: str
    start: int
    end: int


# A derived slice longer than this still holds more than one résumé (real ones in
# the pilot run 1–6 pages). Kept in step with ingest.MAX_RESUME_PAGES, which is what
# actually REFUSES to score such a file — this constant only drives reporting.
_MAX_PAGES_PER_RESUME = 15

# Heading-signal thresholds (see _PageText.opens_with_a_heading). Tuned against a real
# 47-candidate packet: inside its one 19-page unseparated span these values found 6
# candidate boundaries where TOC name-matching had found none.
_HEADING_SIZE_RATIO = 1.4   # heading size vs the page's own body text
_HEADING_MAX_WORDS = 5      # a name, not a sentence
_HEADING_LOOKAHEAD = 4      # how far into the page's reading order to look

# Label for a résumé we separated but cannot name from evidence. Deliberately reads
# as a gap the recruiter should close, not as a name.
_UNNAMED_LABEL = "name not detected"


@dataclass
class SplitResult:
    split: bool
    source: str | None = None
    derived: list[str] = field(default_factory=list)
    method: str | None = None
    # Reporting (2026-07-30): how many résumés the TOC promised vs what we could
    # actually separate, so a shortfall is visible instead of silent.
    expected: int | None = None
    duplicates_dropped: int = 0
    oversized: list[str] = field(default_factory=list)
    # Per-slice identity computed WHILE writing (name -> {"hash", "tokens"}), so a
    # caller maintaining its own de-dup sets (the batch-prep worker) can extend
    # them without reading the just-written files back off the share.
    slice_meta: dict[str, dict] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.derived)

    @property
    def shortfall(self) -> int:
        """TOC names that produced NO résumé and were not a known duplicate — the
        genuinely unaccounted ones the recruiter may need to upload individually.
        Candidates dropped because they are already in the batch (the same person
        in two exports) are NOT a shortfall; they are counted separately."""
        expected = self.expected or (len(self.derived) + self.duplicates_dropped)
        return max(0, expected - len(self.derived) - self.duplicates_dropped)


# --- PDF text ---------------------------------------------------------------------


def _read_pages(data: bytes, limit: int | None = None) -> tuple[list[str], int]:
    """(extracted text for the first `limit` pages, total page count).

    `limit` exists so a caller can decide "is this a Bulk Print?" — a question the
    first pages answer — without paying for a text pass over the whole document.
    The page COUNT is always exact; counting pages does not extract them."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    texts: list[str] = []
    for page in (reader.pages if limit is None else reader.pages[:limit]):
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — one bad page shouldn't sink the whole file
            texts.append("")
    return texts, total


def _page_texts(data: bytes, limit: int | None = None) -> list[str]:
    return _read_pages(data, limit)[0]


class _PageText:
    """Per-page text for ONE document, extracted at most once per page.

    Detection reads the head pages to answer "is this a Bulk Print?", and then — only
    for a real Bulk Print — needs the whole document. Done naively that re-extracts
    the head, and text extraction is ~95% of a split's cost, so the overlap was pure
    waste (measured: the two passes together were 25s of a 54s packet).

    Deliberately not an lru_cache on the module: the cache lives and dies with one
    document, so a 500-résumé batch cannot accumulate 70 MB of page text in memory.
    """

    def __init__(self, data: bytes) -> None:
        from pypdf import PdfReader

        self._reader = PdfReader(io.BytesIO(data))
        self._cache: dict[int, str] = {}

    @property
    def total(self) -> int:
        return len(self._reader.pages)

    def page(self, index: int) -> str:
        if index not in self._cache:
            try:
                self._cache[index] = self._reader.pages[index].extract_text() or ""
            except Exception:  # noqa: BLE001 — one bad page shouldn't sink the file
                self._cache[index] = ""
        return self._cache[index]

    def head(self, limit: int) -> list[str]:
        return [self.page(i) for i in range(min(limit, self.total))]

    def all(self) -> list[str]:
        return [self.page(i) for i in range(self.total)]

    def opens_with_a_heading(self, index: int) -> bool:
        """Does this page OPEN with text much larger than its own body text, short
        enough to be a name? A résumé's own first page usually does.

        Scale-invariant on purpose, and that matters: a Bulk Print concatenates
        résumés each authored in a different tool, so absolute font size and
        y-coordinates are NOT comparable between them. Measured across one real
        packet, body text per page ran 1.0, 10.5, 12.0, 15.4 and 327.7 pt, and
        y-positions ran 25 to 32402. Only a page-relative comparison survives that.

        NOT a general boundary detector — measured recall against known starts in a
        real packet was 5 of 15, because plenty of résumés head their first page with
        ordinary-sized text, a header table, or a scan with no text at all. It earns
        its place only as a TIE-BREAKER inside a span already known to hold several
        résumés (see _subdivide_oversized)."""
        from collections import Counter

        spans: list[tuple[float, int]] = []  # (effective size, word count)

        def visit(text, _cm, tm, _font_dict, font_size) -> None:
            if not text or not text.strip():
                return
            try:
                scale = abs(tm[0]) if tm and tm[0] else 1.0
                # A text matrix can carry the scale instead of the Tf size; ignore
                # absurd multipliers, which are page-level transforms, not type size.
                effective = float(font_size) * (scale if 0 < scale < 5 else 1.0)
            except (TypeError, ValueError, IndexError):
                return
            spans.append((round(effective, 1), len(text.split())))

        try:
            self._reader.pages[index].extract_text(visitor_text=visit)
        except Exception:  # noqa: BLE001 — no signal is a valid answer, never a crash
            return False
        if len(spans) < 2:
            return False  # a scan or near-empty page tells us nothing
        body = Counter(size for size, _ in spans).most_common(1)[0][0]
        if not body:
            return False
        # Among the first few spans (the top of the page in reading order), is there
        # one clearly larger than body text and only a few words long?
        opening = spans[:_HEADING_LOOKAHEAD]
        size, words = max(opening, key=lambda s: s[0])
        return (size / body) >= _HEADING_SIZE_RATIO and 1 <= words <= _HEADING_MAX_WORDS


# --- iCIMS Table-of-Contents detection --------------------------------------------


def _parse_toc(page_texts: list[str]) -> tuple[list[str], int] | None:
    """(ordered candidate names, last TOC page index) for an iCIMS Bulk Print, or
    None when the file isn't one. Requires the 'Bulk Print/Table of Contents'
    signature AND at least two numbered '- Resume' entries, so a normal résumé that
    merely contains a numbered list is never mistaken for a bulk file."""
    names: list[str] = []
    last_toc_page = -1
    signature = False
    for idx, text in enumerate(page_texts[:_MAX_TOC_PAGES]):
        if _TOC_SIGNATURE_RE.search(text):
            signature = True
        page_entries = [m.group(2).strip() for m in _TOC_ENTRY_RE.finditer(text)]
        if page_entries:
            names.extend(page_entries)
            last_toc_page = idx
        elif names:
            break  # entries ended -> TOC is over
    if signature and len(names) >= 2:
        return names, last_toc_page
    return None


def looks_like_bulk_print(text: str) -> bool:
    """True when raw extracted text carries the iCIMS Bulk Print signature AND at
    least two '<n>. <name> - Resume' entries — i.e. many résumés concatenated into
    one file. Used both to split (above) and, as a safety net, to REFUSE scoring an
    un-split bulk file as a single candidate (see ingest.single_resume_problem)."""
    if not _TOC_SIGNATURE_RE.search(text or ""):
        return False
    return len(_TOC_ENTRY_RE.findall(text or "")) >= 2


def _fold_name(text: str) -> str:
    """Casefold and strip accents. A Bulk Print PDF is extracted by pypdf, which
    routinely loses diacritics, so the table of contents can say "Kovács Péter"
    where the page says "Kylie Chaaya" — comparing them as typed finds neither."""
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text)
                       if not unicodedata.combining(c))
    return stripped.casefold()


def _name_tokens(name: str) -> list[str]:
    """The parts of a name worth searching a page for.

    Unicode letters, three or more (2026-09-12). This split on ``[^a-z0-9]`` and
    kept anything two characters long, so "Kovács Péter" became
    ["kov", "cs", "ter"] — and a token is matched as a bare substring, so "cs" and
    "ter" hit almost any page of English or Hungarian text. The failure was not
    "no match" but PROMISCUOUS ones: candidates anchored to each other's pages,
    and résumé content attributed to the wrong person."""
    return re.findall(r"[^\W\d_]{3,}", _fold_name(name))


def _token_on_page(token: str, folded_page: str) -> bool:
    """One name part, bounded by non-letters so a short part cannot match inside a
    longer word ("ter" must not find "interim")."""
    return re.search(r"(?<![^\W\d_])" + re.escape(token) + r"(?![^\W\d_])",
                     folded_page) is not None


def _page_has_name(text: str, name: str) -> bool:
    """Fuzzy, position-agnostic name match: the first name token must appear and, when
    the name has a distinct surname token, at least one of those too. Tolerates
    nicknames/middle names and two-column layouts where the name isn't the first line."""
    low = _fold_name(text)
    tokens = _name_tokens(name)
    if not tokens or not _token_on_page(tokens[0], low):
        return False
    others = [t for t in tokens[1:] if t != tokens[0]]
    if others and not any(_token_on_page(t, low) for t in others):
        return False
    return True


# Split only when at least this fraction of TOC names can be located by name; below
# it we don't trust the mapping and defer to the LLM / leave the file unsplit.
_MIN_ANCHOR_FRACTION = 0.5


def _interpolate_starts(n: int, anchors: dict[int, int], first_page: int,
                        last_page: int) -> list[int]:
    """Start page for every one of `n` résumés given a subset of name→page anchors.
    Un-anchored names are placed by even interpolation between the surrounding
    anchors, then the whole list is forced non-decreasing and in range — so a
    confidently-detected TOC yields `n` résumés even when a few names couldn't be
    pinpointed in pypdf's text (far better than collapsing all of them into one file)."""
    starts: list[int | None] = [None] * n
    for i, page in anchors.items():
        starts[i] = page
    known = sorted(anchors.items())  # [(name_index, page), …] ascending
    # names before the first anchor -> start of the résumé body
    for i in range(known[0][0]):
        starts[i] = first_page
    # gaps between consecutive anchors (and the tail after the last one)
    for a, (i0, p0) in enumerate(known):
        i1, p1 = known[a + 1] if a + 1 < len(known) else (n, last_page + 1)
        gap = i1 - i0
        for k in range(1, gap):
            interp = p0 + round(k * (p1 - p0) / gap)
            starts[i0 + k] = min(last_page, max(p0 + k, interp))
    out: list[int] = []
    prev = first_page
    for value in starts:
        page = prev if value is None or value < prev else value
        page = min(page, last_page)
        out.append(page)
        prev = page
    return out


def _segments_from_toc(page_texts: list[str], names: list[str],
                       toc_end: int) -> list[Segment] | None:
    """Map each TOC name to a page span. Résumés follow the TOC in order, each on a
    fresh page; we walk forward locating each name after the previous match. Names
    that can't be located are filled in by interpolation (`_interpolate_starts`) as
    long as a majority anchored — so the file still splits into len(names) résumés.
    Returns None only when too few names anchor to trust the mapping (caller then
    tries the LLM, else leaves the file unsplit)."""
    first_page, last_page = toc_end + 1, len(page_texts) - 1
    if first_page > last_page:
        return None
    anchors: dict[int, int] = {}
    cursor = first_page
    for i, name in enumerate(names):
        for page in range(cursor, len(page_texts)):
            if _page_has_name(page_texts[page], name):
                anchors[i] = page
                cursor = page + 1
                break
    if len(anchors) < max(2, int(len(names) * _MIN_ANCHOR_FRACTION)):
        return None
    starts = _interpolate_starts(len(names), anchors, first_page, last_page)
    # In a Bulk Print every résumé begins on a FRESH page, so two candidates can
    # never share a start page. Interpolation can still collapse several
    # un-anchored names onto one page (it clamps to `last_page` when the remaining
    # pages run out), and each collapsed pair used to be written as a
    # BYTE-IDENTICAL slice — two files, one content hash, so they silently merged
    # into a single candidate downstream and the batch count over-reported. Keep
    # one segment per distinct start page and report the shortfall instead.
    segments: list[Segment] = []
    collapsed: list[str] = []
    for i, name in enumerate(names):
        if segments and starts[i] <= segments[-1].start:
            collapsed.append(name)  # no page of its own -> not a separable résumé
            continue
        end = (starts[i + 1] - 1) if i + 1 < len(names) else last_page
        segments.append(Segment(label=name, start=starts[i], end=max(starts[i], end)))
    # Re-close each span against the NEXT kept segment, so dropping a collapsed
    # name never leaves a gap (or an overlap) in the page coverage.
    for a, seg in enumerate(segments):
        seg.end = (segments[a + 1].start - 1) if a + 1 < len(segments) else last_page
        seg.end = max(seg.start, seg.end)
    if collapsed:
        log.warning("resume_split: %d of %d TOC names could not be given their own page "
                    "(%s…) — emitting %d résumés; the rest stay inside a neighbour's "
                    "slice and are reported as a shortfall.",
                    len(collapsed), len(names), collapsed[0], len(segments))
    return segments


def _subdivide_oversized(segments: list[Segment], names: list[str],
                         pages: "_PageText") -> list[Segment]:
    """Second chance for the names that got no page of their own.

    THE CASE THIS FIXES, from the pilot's own batch: a 47-name packet produced one
    19-page slice holding a candidate plus four neighbours. The UI reported it
    honestly ("several résumés", refused at screening), but the four people were
    unreachable — the recruiter had to find and upload them by hand.

    Why the first pass missed them: `_segments_from_toc` walks names in TOC order
    with a forward cursor, so once a name fails to match, later matches for it are
    never looked for again. Searching just the oversized span, unconstrained, finds
    many of them — and finding the name is what makes the new piece SAFE TO LABEL.

    Two signals, strongest first:
      1. the page actually contains the missing name (verifiable — we name the piece);
      2. failing that, the page opens with a heading (see opens_with_a_heading) —
         we cut but leave the piece unnamed rather than guess whose it is.

    Deliberately conservative. It only ever touches spans ALREADY reported as
    holding several résumés, never makes more cuts than there are missing names, and
    never puts a name on a piece that does not contain it. A wrong name on a
    candidate's file is worse than an unnamed one, and an unnamed piece still becomes
    its own candidate, scored on its own merits."""
    used_labels = {s.label for s in segments if s.label}
    missing = [n for n in names if n not in used_labels]
    if not missing:
        return segments

    out: list[Segment] = []
    for seg in segments:
        span = seg.end - seg.start + 1
        if not missing or span <= _MAX_PAGES_PER_RESUME:
            out.append(seg)
            continue
        # Candidate cut points inside this span (never its first page — that one
        # belongs to the résumé the TOC already anchored here).
        interior = range(seg.start + 1, seg.end + 1)
        cuts: dict[int, str] = {}
        for name in list(missing):
            for page in interior:
                if page not in cuts and _page_has_name(pages.page(page), name):
                    cuts[page] = name
                    missing.remove(name)
                    break
        for page in interior:
            if not missing:
                break
            if page not in cuts and pages.opens_with_a_heading(page):
                # Separate it, but do not claim to know whose it is. Position in the
                # packet WOULD imply a TOC name (Bulk Print keeps order, and the
                # recovered count matches the TOC exactly), but implying is not
                # evidence: putting the wrong person's name on a résumé is the one
                # error here that misattributes a real candidate, so the recruiter
                # gets an obviously-unnamed row to name instead of a plausible lie.
                cuts[page] = _UNNAMED_LABEL
                missing.pop(0)
        if not cuts:
            out.append(seg)
            continue
        bounds = [seg.start] + sorted(cuts)
        for i, start in enumerate(bounds):
            end = (bounds[i + 1] - 1) if i + 1 < len(bounds) else seg.end
            out.append(Segment(label=(seg.label if i == 0 else cuts[start]),
                               start=start, end=max(start, end)))
        log.info("resume_split: subdivided a %d-page slice into %d résumés "
                 "(%d recovered by name, %d by heading)", span, len(bounds),
                 sum(1 for v in cuts.values() if v != _UNNAMED_LABEL),
                 sum(1 for v in cuts.values() if v == _UNNAMED_LABEL))
    return out


_SNIPPET_SOURCE_CHARS = 600  # enough of a page's opening lines to redact as lines
_SNIPPET_CHARS = 180
# Words a résumé section heading is made of. A page that OPENS with one of these is a
# continuation page, not a new letterhead — and résumé redaction would otherwise read
# any short first line as a name and mask it, which is the very signal the boundary
# model looks for ("[CANDIDATE]" at the top of a page = a new résumé).
_HEADING_WORDS = frozenset("""
about academic academics accomplishments achievement achievements activities additional
affiliations and areas award awards bulk career certificate certificates certification
certifications competencies competency contact cont continued core curricular declaration
details education educational employment experience expertise extra extracurricular held
highlights history hobbies information interests internship internships key language
languages me memberships objective of other overview page personal positions previous
print professional profile project projects publications qualification qualifications
references responsibilities roles skill skills software strengths summary systems table
technical the tools training trainings volunteer work contents
""".split())


def _is_heading(line: str) -> bool:
    words = re.findall(r"[^\W\d_]+", line.casefold())
    return bool(words) and all(w in _HEADING_WORDS for w in words)


def _boundary_prompt(page_texts: list[str], names: list[str]) -> str:
    """The LLM fallback's prompt, with the candidates' identities taken out.

    Until 2026-10-14 it sent each page's opening characters and the table of
    contents' names as they were — names, emails and phone numbers reached Azure
    OpenAI before any redaction, for every candidate in the packet. Now each page's
    opening lines go through the same redaction a résumé does — which masks that
    page's own letterhead line, contact details and labelled personal lines — and the
    table of contents' names are masked wherever they appear. (Not every page's first
    line: on a continuation page that line is ordinary words, and masking them across
    the packet would hide the headings the model finds boundaries by.) The model is
    told how many résumés there are and in what order; the restarted letterheads
    ("[CANDIDATE]") and section headings are what it finds the boundaries by."""
    from resume_screener.redact import _NAME_STOPWORDS, letterhead_tokens, redact_text

    toc_tokens: set[str] = set()
    for name in names:
        toc_tokens |= {t for t in re.findall(r"[^\W\d_]{3,}", name)
                       if t.casefold() not in _NAME_STOPWORDS}
    snippets = []
    for i, text in enumerate(page_texts):
        source = text[:_SNIPPET_SOURCE_CHARS]
        first = next((line.strip() for line in source.splitlines() if line.strip()), "")
        page_tokens = set() if _is_heading(first) else letterhead_tokens(first)
        head = redact_text(source, "", "", name_tokens=toc_tokens | page_tokens).redacted_text
        snippets.append(f"[page {i + 1}] {' '.join(head.split())[:_SNIPPET_CHARS]}")
    return (
        f"A single PDF concatenates {len(names)} candidates' résumés, one after another, "
        "in this order (names withheld):\n"
        + "\n".join(f"{i + 1}. [CANDIDATE]" for i in range(len(names)))
        + "\n\nHere is how each page begins, with personal details removed — a new "
        "résumé usually opens with the candidate's name line ([CANDIDATE]) and a fresh "
        "summary or experience section:\n"
        + "\n".join(snippets)
        + "\n\nReturn start_pages: the 1-based page number where each résumé begins, "
        "in the same order as the list above."
    )


def _llm_boundaries(page_texts: list[str], names: list[str]) -> list[Segment] | None:
    """Ask the model for each candidate's start page when name-matching failed.
    Lazy + non-fatal: returns None offline or on any error (the file then stays
    unsplit rather than being split wrongly). The prompt carries no names or contact
    details (see _boundary_prompt)."""
    try:
        from pydantic import BaseModel

        from resume_screener.azure_clients import get_openai_client, parse_structured
        from resume_screener.config import api_keys_for, load_settings

        class _Starts(BaseModel):
            start_pages: list[int]  # 1-based page number where each candidate begins, in TOC order

        prompt = _boundary_prompt(page_texts, names)
        settings = load_settings()
        keys = api_keys_for(settings)
        info = get_openai_client(settings, keys)
        parsed = parse_structured(
            info.client, settings.azure_openai_chat_deployment,
            [{"role": "user", "content": prompt}], _Starts,
        )
        starts = [p - 1 for p in parsed.start_pages]
        if len(starts) != len(names) or starts != sorted(starts):
            return None
        if starts[0] < 0 or starts[-1] >= len(page_texts):
            return None
        segments = []
        for i, (name, start) in enumerate(zip(names, starts)):
            end = (starts[i + 1] - 1) if i + 1 < len(starts) else len(page_texts) - 1
            segments.append(Segment(label=name, start=start, end=max(start, end)))
        return segments
    except Exception as exc:  # noqa: BLE001 — no Azure / refusal -> no split, never fail the upload
        from resume_screener.config import ConfigError

        (log.warning if isinstance(exc, ConfigError) else log.info)(
            "resume_split LLM boundary fallback unavailable (%s)", exc)
        return None


def _detect_with_method(data: bytes) -> tuple[list[Segment], str, int | None, list[str]]:
    """(segments, method, expected, page_texts). One segment (the whole file) means
    'not a multi-résumé file' and method 'single'; the caller then does nothing.
    `expected` is how many résumés the Bulk Print TOC promised (None when the file
    isn't a bulk print) — the yardstick a shortfall is reported against.

    `page_texts` is the parent's per-page text, returned so _apply_split can derive
    each slice's contact tokens from text it already has rather than re-parsing every
    written slice (empty for the 'single' answers, which need no slicing)."""
    try:
        pages = _PageText(data)
    except Exception as exc:  # noqa: BLE001 — unreadable PDF -> treat as single, never crash
        log.info("resume_split couldn't read a PDF (%s) — leaving it as one résumé", exc)
        return [Segment("resume", 0, 0)], "single", None, []
    return _detect_on_pages(pages)


def examine_pdf(data: bytes) -> tuple[list[Segment], str, int | None, str]:
    """(segments, method, expected, full_text) — ONE text-extraction pass answering
    both intake questions about a PDF: "is this a multi-résumé file?" and "what
    contact identity does it state?".

    full_text is the text of EVERY page joined with newlines — the same pypdf
    extractor over the same bytes contacts._text_from_bytes reads, so contact
    tokens derived from it are the same tokens (pinned by test). Before this seam
    existed, every uploaded packet paid two full text passes: one for contact
    de-dup (whose tokens a multi-candidate file then discards) and one for split
    detection. An unreadable file returns a single segment and "" — exactly the
    cases where contact extraction would also have yielded nothing."""
    try:
        pages = _PageText(data)
    except Exception as exc:  # noqa: BLE001 — unreadable PDF -> single + no text, never crash
        log.info("resume_split couldn't read a PDF (%s) — leaving it as one résumé", exc)
        return [Segment("resume", 0, 0)], "single", None, ""
    segments, method, expected, _partial = _detect_on_pages(pages)
    try:
        full_text = "\n".join(pages.all())
    except Exception:  # noqa: BLE001 — a file whose page tree breaks mid-walk has no text
        full_text = ""
    return segments, method, expected, full_text


def _detect_on_pages(pages: _PageText) -> tuple[list[Segment], str, int | None, list[str]]:
    try:
        # Read only the pages the TOC can live on. _parse_toc never looks past
        # _MAX_TOC_PAGES, so this decides "bulk print or not" on exactly the same
        # evidence as a full read — and "not" is the overwhelmingly common answer
        # (every ordinary résumé, re-checked on every batch rescan). Extracting a
        # whole document to conclude it has no TOC was the single most expensive
        # thing an upload did. The full text is read below, only once a TOC is real —
        # and _PageText memoizes, so the head pages are never extracted twice.
        head, total_pages = pages.head(_MAX_TOC_PAGES), pages.total
    except Exception as exc:  # noqa: BLE001 — unreadable PDF -> treat as single, never crash
        log.info("resume_split couldn't read a PDF (%s) — leaving it as one résumé", exc)
        return [Segment("resume", 0, 0)], "single", None, []
    if total_pages <= 1:
        return [Segment("resume", 0, max(0, total_pages - 1))], "single", None, []
    toc = _parse_toc(head)
    if toc is None:
        return [Segment("resume", 0, total_pages - 1)], "single", None, []  # no TOC -> single
    page_texts = pages.all()  # a real Bulk Print — the head pages come from the cache
    names, toc_end = toc
    segments = _segments_from_toc(page_texts, names, toc_end)
    method = "icims_toc"
    if segments is None:
        segments = _llm_boundaries(page_texts, names)
        method = "icims_toc+llm"
    if not segments or len(segments) <= 1:
        # A Bulk Print TOC was found but we couldn't map résumés to pages. Never
        # silently treat it as one candidate — say so loudly; ingest's scoring guard
        # then refuses to score the un-split file rather than emitting a bogus score.
        log.warning("resume_split: detected a %d-candidate Bulk Print TOC but could not "
                    "map résumés to pages; leaving the file unsplit.", len(names))
        return [Segment("resume", 0, len(page_texts) - 1)], "single", len(names), page_texts
    # Second chance for names that got no page of their own — they would otherwise
    # stay buried inside a neighbour's slice, visible only as a shortfall warning.
    if len(segments) < len(names):
        before = len(segments)
        segments = _subdivide_oversized(segments, names, pages)
        if len(segments) > before:
            method += "+heading"
    for seg in segments:
        seg.label = seg.label or "resume"
    log.info("resume_split: %s -> %d résumés from a %d-name TOC", method, len(segments),
             len(names))
    return segments, method, len(names), page_texts


def detect_boundaries(data: bytes) -> list[Segment]:
    """Page spans for each résumé in a PDF. One segment (the whole file) means
    'not a multi-résumé file' — the caller then does nothing."""
    return _detect_with_method(data)[0]


# --- manifest ---------------------------------------------------------------------


def _manifest_path(folder: Path) -> Path:
    return Path(folder) / MANIFEST_NAME


def read_manifest(folder: str | Path) -> dict:
    """{original_name: {source_stored, derived[], count, method, detected_at}}; {} if none."""
    path = _manifest_path(Path(folder))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(folder: Path, data: dict) -> None:
    path = _manifest_path(folder)
    with file_lock(path):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


# --- splitting --------------------------------------------------------------------


def _sanitize(label: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9 .,_-]+", "", label).strip(" .-_")
    return clean or "resume"


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix = target.stem, target.suffix
    counter = 2
    while target.exists():
        target = folder / f"{stem}-{counter}{suffix}"
        counter += 1
    return target


def _apply_split(folder: Path, source_path: Path, data: bytes,
                 segments: list[Segment], method: str, now: str,
                 expected: int | None = None,
                 known_contacts: set[str] | None = None) -> SplitResult:
    from pypdf import PdfReader, PdfWriter

    from resume_screener.ats.local_folder import _batch_contacts
    from resume_screener.contacts import (
        contact_tokens, holds_many_candidates, remember_tokens,
    )

    reader = PdfReader(io.BytesIO(data))
    derived: list[str] = []
    oversized: list[str] = []
    seen_slices: dict[str, str] = {}  # content hash -> the file already written
    duplicates = 0
    # Per-PERSON de-dup belongs HERE, on the one-candidate slices — the packet
    # itself is 40+ people, so it can't be identified by contact. This is what
    # catches the same candidate appearing in two different Bulk Print exports.
    # The packet being sliced is excluded: it contains every candidate inside it,
    # so each slice would otherwise match its own source and be dropped.
    # A caller that already maintains the batch's contact set across many packets
    # (the batch-prep worker) passes it in — this both skips a folder re-scan per
    # packet AND is mutated in place, so packet N+1 sees packet N's people.
    if known_contacts is None:
        known_contacts = _batch_contacts(folder, exclude={source_path.name})
    slice_meta: dict[str, dict] = {}
    for idx, seg in enumerate(segments, start=1):
        writer = PdfWriter()
        for page in range(seg.start, seg.end + 1):
            writer.add_page(reader.pages[page])
        buffer = io.BytesIO()
        writer.write(buffer)
        blob = buffer.getvalue()
        # A slice identical to one already written is the SAME bytes, so it would
        # carry the same content-hash candidate_id and silently merge downstream —
        # write it once (the engine's own dedupe rule, applied at the source).
        digest = candidate_id_for(blob)
        if digest in seen_slices:
            duplicates += 1
            continue
        # Tokens come from the WRITTEN SLICE, never from the parent's page text for the
        # same span — even though the latter is already in hand and would halve the cost
        # of a split.
        #
        # Tried and REVERTED (2026-08-26), because it silently hid real candidates. The
        # parent's text for pages 88-90 is NOT the same as the text of the file written
        # from pages 88-90: pypdf's page copy drops some content, and the parent
        # extraction picks up neighbouring text the slice does not contain. Measured on
        # a real 6-packet batch, that gave "Tiare Kumar" a phone belonging to "Vijay
        # Rege" in the ADJACENT segment, and matched a 19-page oversized slice against
        # a candidate in a different packet — 2 of 232 people dropped as "duplicates"
        # they were not.
        #
        # The principle: the written slice IS the candidate's file of record, so identity
        # must be read from it. Deduping on text that is not in the file can hide
        # someone, and no amount of speed is worth that (guardrail §6.3 — no hiding).
        slice_tokens = contact_tokens(blob, ".pdf")
        if slice_tokens and not holds_many_candidates(slice_tokens):
            if slice_tokens & known_contacts:
                duplicates += 1  # this person is already in the batch
                continue
            known_contacts |= slice_tokens
        # NN prefix keeps TOC order in the sorted batch; candidate_name() drops the
        # digits, so the displayed name stays just the person's name.
        target = _free_name(folder, f"{idx:02d} - {_sanitize(seg.label)}.pdf")
        target.write_bytes(blob)
        # Seed the contact cache for the file we just wrote. Without this, the NEXT
        # packet in the same upload re-extracts text from every slice this packet
        # produced (_batch_contacts scans the folder), which is quadratic across a
        # multi-packet upload — the same bug class as the earlier add-résumé 504.
        remember_tokens(target, slice_tokens)
        seen_slices[digest] = target.name
        slice_meta[target.name] = {"hash": digest, "tokens": sorted(slice_tokens)}
        derived.append(target.name)
        # Too long to be one résumé: this slice still holds several candidates (the
        # names inside it couldn't be pinned to their own pages). ingest's
        # single_resume_problem refuses to SCORE it, so it never becomes a bogus
        # candidate — record it here so the batch can say so up front.
        if (seg.end - seg.start + 1) > _MAX_PAGES_PER_RESUME:
            oversized.append(target.name)

    sources_dir = folder / SOURCES_DIRNAME
    sources_dir.mkdir(parents=True, exist_ok=True)
    stored = _free_name(sources_dir, source_path.name)
    os.replace(source_path, stored)

    manifest = read_manifest(folder)
    entry = {
        "source_stored": f"{SOURCES_DIRNAME}/{stored.name}",
        "derived": derived,
        "count": len(derived),
        "method": method,
        "detected_at": now,
    }
    # Honest accounting so "333 files -> 309 in Review" is never a mystery: how many
    # candidates the TOC promised, how many identical slices were dropped, and which
    # slices still hold more than one résumé.
    if expected is not None:
        entry["expected"] = expected
        # Only the names that produced nothing AND weren't a known duplicate are a
        # shortfall the recruiter must act on.
        missing = expected - len(derived) - duplicates
        if missing > 0:
            entry["shortfall"] = missing
    if duplicates:
        entry["duplicates_dropped"] = duplicates
    if oversized:
        entry["oversized"] = oversized
    manifest[source_path.name] = entry
    _write_manifest(folder, manifest)
    if duplicates or oversized or entry.get("shortfall"):
        log.warning("resume_split: %s -> %d résumés (TOC promised %s; %d identical "
                    "slices dropped; %d slice(s) still hold multiple résumés)",
                    source_path.name, len(derived), expected, duplicates, len(oversized))
    return SplitResult(split=True, source=source_path.name, derived=derived, method=method,
                       expected=expected, duplicates_dropped=duplicates,
                       oversized=oversized, slice_meta=slice_meta)


def _source_hashes(folder: Path) -> set[str]:
    from resume_screener.ats.local_folder import cached_candidate_id

    sources_dir = folder / SOURCES_DIRNAME
    if not sources_dir.exists():
        return set()
    out: set[str] = set()
    for path in sources_dir.iterdir():
        if path.is_file():
            try:
                out.add(cached_candidate_id(path))  # stored bulk files are the big ones
            except OSError:
                continue
    return out


# Files already examined and found to be ordinary single résumés, keyed on their
# identity on disk: abs path -> (size, mtime_ns).
#
# split_batch_folder runs over the WHOLE batch on EVERY upload, and re-examining a
# file it has already judged is pure waste: the verdict is a function of the bytes,
# and any change to them moves size/mtime and re-examines it. Without this, adding
# one résumé to a batch of 200 re-read and re-extracted all 200.
_SINGLE_CACHE: dict[str, tuple[int, int]] = {}


def _file_state(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _known_single(path: Path) -> bool:
    """True when this exact file was already judged 'not a multi-résumé file'."""
    state = _file_state(path)
    return state is not None and _SINGLE_CACHE.get(str(path.resolve())) == state


def _remember_single(path: Path) -> None:
    state = _file_state(path)
    if state is not None:
        _SINGLE_CACHE[str(path.resolve())] = state


def split_batch_folder(folder: str | Path, now: str | None = None) -> list[SplitResult]:
    """Split any un-split multi-résumé PDF sitting in the batch. Idempotent:
    files already produced by a split (recorded in the manifest) are skipped, and a
    re-uploaded already-split bulk file (same bytes as a stored source) is dropped.
    Returns one SplitResult per file that was split (empty when nothing changed)."""
    from resume_screener.ats.local_folder import batch_files

    folder = Path(folder)
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = read_manifest(folder)
    already_derived = {name for entry in manifest.values() for name in entry.get("derived", [])}
    source_hashes = _source_hashes(folder)

    results: list[SplitResult] = []
    for path in batch_files(folder):
        if path.suffix.lower() != ".pdf":
            continue  # DOCX (and anything else) stays a single candidate
        if path.name in already_derived:
            continue  # a product of a previous split
        if _known_single(path):
            continue  # judged on an earlier pass and unchanged since — not a bulk file
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if candidate_id_for(data) in source_hashes:
            path.unlink()  # re-upload of an already-split bulk file — drop the duplicate
            continue
        segments, method, expected, _page_texts_unused = _detect_with_method(data)
        if len(segments) <= 1:
            _remember_single(path)
            continue
        results.append(_apply_split(folder, path, data, segments, method, now, expected))
        already_derived.update(results[-1].derived)
    return results


def batch_groups(folder: str | Path) -> list[dict]:
    """Grouping view for get_batch: [{source, derived:[names], count}] for each bulk
    file whose derived résumés are still present. Empty when nothing was split, so a
    plain batch renders exactly as before."""
    folder = Path(folder)
    from resume_screener.ats.local_folder import batch_files

    present = {p.name for p in batch_files(folder)}
    groups: list[dict] = []
    for source, entry in read_manifest(folder).items():
        derived = [d for d in entry.get("derived", []) if d in present]
        if not derived:
            continue
        group = {"source": source, "derived": derived, "count": len(derived)}
        # Honest accounting (2026-07-30): what the Bulk Print TOC promised vs what
        # we could actually separate, so "N files in, fewer candidates out" is
        # explained on the Candidates tab instead of being discovered in Review.
        expected = entry.get("expected")
        dropped = entry.get("duplicates_dropped") or 0
        if isinstance(expected, int) and expected > 0:
            group["expected"] = expected
            # How many of this packet's people are IN the batch — the new files plus
            # the ones already here from another export. This, not `count`, is the
            # number a recruiter arrives at by counting, so it is the number the
            # header states. Reporting `count` against `expected` read as a loss
            # ("split into 14 résumés of 15") when all 15 people were present and
            # nothing had gone wrong.
            group["present"] = len(derived) + dropped
            # Same rule as SplitResult.shortfall: a candidate kept once (already in
            # the batch from another export) is NOT missing, so it never raises the
            # "couldn't be separated" warning.
            missing = expected - len(derived) - dropped
            if missing > 0:
                group["shortfall"] = missing
        oversized = [name for name in entry.get("oversized", []) if name in present]
        if oversized:
            group["oversized"] = oversized
        if entry.get("duplicates_dropped"):
            group["duplicates_dropped"] = entry["duplicates_dropped"]
        groups.append(group)
    return groups


def cleanup_removed(folder: str | Path) -> None:
    """Keep the manifest and stored sources honest after files are removed: drop
    manifest groups whose derived résumés are all gone, and delete the orphaned
    stored original + the _sources dir when empty. Safe to call after any batch edit."""
    folder = Path(folder)
    from resume_screener.ats.local_folder import batch_files

    present = {p.name for p in batch_files(folder)}
    manifest = read_manifest(folder)
    changed = False
    for source in list(manifest.keys()):
        entry = manifest[source]
        if not any(d in present for d in entry.get("derived", [])):
            stored = entry.get("source_stored")
            if stored:
                stored_path = folder / stored
                if stored_path.exists():
                    stored_path.unlink()
            del manifest[source]
            changed = True
    if changed:
        _write_manifest(folder, manifest)
    sources_dir = folder / SOURCES_DIRNAME
    if sources_dir.exists() and not any(sources_dir.iterdir()):
        sources_dir.rmdir()
