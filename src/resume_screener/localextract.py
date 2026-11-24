"""Local-first résumé text extraction (InfoSec question 6, 2026-10-14).

InfoSec asked whether a résumé is redacted before it reaches Document Intelligence.
It was not: every file went to DI whole, name and phone and address included, and
redaction ran on the text that came back. Most résumés need no OCR at all — they are
digital PDFs or Word files that already carry their text. This module reads that text
on our own machine, so a text-bearing résumé reaches redaction without leaving the app,
and the rest of the pipeline sees the same shape it always did (raw text → redact →
models).

What still goes to Document Intelligence, and why: a file with no usable text of its
own — a scan, a phone photo, a PDF whose letters are drawn as shapes — has nothing to
read here, so DI OCRs it exactly as before. That is the stated residual.

Routing, per file (`LocalRead.route`; "text" = read here, anything else = DI):
- PDF, page by page: at least `threshold` letters and digits make a TEXT page; below
  it, a page carrying a picture is IMAGE-ONLY (a scan) and one without is BLANK. The
  file goes to DI when image-only pages OUTNUMBER text pages ("image-only"), when the
  whole file yields less than one page's worth of text ("no-text-layer"), or when the
  text it yields is mostly junk ("garbled-text"). A lone sparse page with a picture on
  it — the signed declaration many Indian résumés end with — does not send a
  full-text résumé away; its page shows in the stats.
- DOCX: header, then the body in document order (paragraphs, tables, text boxes,
  content controls), then footer. Under `threshold` letters and digits → DI ("image-
  only" when the file holds pictures, else "no-text-layer").
- Anything unreadable or of another type → DI ("unreadable" / "unsupported").

pypdf's PLAIN mode, not layout mode: layout mode pads text to its x-position, so a
two-column page lands both columns on one line, and the redaction rule that removes a
labelled personal line ("Date of birth: …") would then delete the job content sitting
beside it in the other column.

Read quality: DI reports a per-word OCR confidence, which the confidence rating uses.
Local text has no OCR, so `text_quality` measures the text layer instead — the share
of characters that are not replacement characters, control codes, private-use glyphs
or unmapped `(cid:N)` tokens. Clean digital text reads 1.0, as DI's ~0.99 did.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resume_screener.config import AppConfig
    from resume_screener.modes import Check

log = logging.getLogger(__name__)

# Part of the extraction cache key: bump when routing or reading changes, so text read
# by an older rule is never served as if the new one had produced it.
LOCAL_EXTRACT_VERSION = "le1"

# Below this read quality a text layer is treated as unreadable and the file goes to DI.
# The same figure the scoring model uses for a "low" read (confidence.low_below).
MIN_LOCAL_QUALITY = 0.75

LOCAL = "text"  # the one route that stays on this machine


@dataclass(frozen=True)
class LocalRead:
    text: str
    page_count: int
    route: str
    stats: dict = field(default_factory=dict)
    quality: float | None = None

    @property
    def local(self) -> bool:
        return self.route == LOCAL


def _alnum(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


def text_quality(text: str) -> float | None:
    """1 − the share of junk among the non-space characters; None for no text.

    Junk = U+FFFD, control codes, private-use and unassigned code points, plus each
    `(cid:N)` token counted as eight bad characters (roughly its own length — the
    token IS the failure to map a glyph to a letter)."""
    chars = "".join(text.split())
    if not chars:
        return None
    bad = sum(1 for ch in chars
              if ch == "�" or unicodedata.category(ch) in ("Cc", "Co", "Cn"))
    bad += 8 * text.count("(cid:")
    return round(max(0.0, 1.0 - bad / len(chars)), 4)


# --- PDF -------------------------------------------------------------------------------

def page_has_image(page) -> bool:
    """Whether a pypdf page draws any image (XObject, nested Form XObject or inline).
    When pypdf cannot tell, assume it does: the page then counts as a scan, which can
    only ever send a file to OCR, never lose its content."""
    try:
        return len(page.images) > 0
    except Exception:  # noqa: BLE001 — a malformed resource dictionary
        return True


def read_pdf(data: bytes, threshold: int) -> LocalRead:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")  # an owner-password-only file opens with an empty password
    texts: list[str] = []
    chars: list[int] = []
    kinds: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — one bad page shouldn't sink the file
            text = ""
        n = _alnum(text)
        texts.append(text)
        chars.append(n)
        # The image check walks the page's resources; only a low-text page needs it.
        kinds.append("text" if n >= threshold else ("image" if page_has_image(page) else "blank"))
    text_pages, image_pages = kinds.count("text"), kinds.count("image")
    text = "\n\n".join(t.strip() for t in texts if t.strip())
    quality = text_quality(text)
    if image_pages > text_pages:
        route = "image-only"
    elif sum(chars) < threshold:
        route = "no-text-layer"
    elif quality is not None and quality < MIN_LOCAL_QUALITY:
        route = "garbled-text"
    else:
        route = LOCAL
    stats = {"page_count": len(texts), "text_pages": text_pages,
             "image_only_pages": image_pages, "blank_pages": kinds.count("blank"),
             "chars_per_page": chars}
    return LocalRead(text if route == LOCAL else "", len(texts), route, stats,
                     quality if route == LOCAL else None)


# --- DOCX ------------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_W_P, _W_T, _W_TBL, _W_TR, _W_TC = _W + "p", _W + "t", _W + "tbl", _W + "tr", _W + "tc"
_W_SDT, _W_SDT_CONTENT, _W_TXBX = _W + "sdt", _W + "sdtContent", _W + "txbxContent"
_W_TABS = {_W + "tab", _W + "ptab"}
_W_BREAKS = {_W + "br", _W + "cr"}
_W_HYPHEN = _W + "noBreakHyphen"


def _run_text(paragraph) -> str:
    """A paragraph's own text — runs, hyperlinks, tracked insertions — without the
    text boxes anchored in it (those are read as their own lines) and without the
    `mc:Fallback` copy Word writes beside every modern shape (it would read twice).
    Deleted text (`w:delText`) and field codes (`w:instrText`) are other tags, so
    they never appear."""
    out: list[str] = []

    def walk(node) -> None:
        for child in node:
            tag = child.tag
            if tag == _W_T:
                out.append(child.text or "")
            elif tag in _W_TABS:
                out.append("\t")
            elif tag in _W_BREAKS:
                out.append("\n")
            elif tag == _W_HYPHEN:
                out.append("-")
            elif tag in (_W_TXBX, _MC_FALLBACK):
                continue
            else:
                walk(child)

    walk(paragraph)
    return "".join(out)


def _text_boxes(node) -> list:
    found: list = []

    def walk(el) -> None:
        for child in el:
            if child.tag == _MC_FALLBACK:
                continue
            if child.tag == _W_TXBX:
                found.append(child)  # its own nested boxes are read with it
                continue
            walk(child)

    walk(node)
    return found


def _children(node, tag: str) -> list:
    """Direct children with `tag`, looking through content controls (w:sdt)."""
    out = []
    for child in node:
        if child.tag == tag:
            out.append(child)
        elif child.tag == _W_SDT:
            content = child.find(_W_SDT_CONTENT)
            if content is not None:
                out.extend(_children(content, tag))
    return out


def _block_lines(container) -> list[str]:
    lines: list[str] = []
    for child in container:
        tag = child.tag
        if tag == _W_P:
            own = _run_text(child).strip()
            if own:
                lines.append(own)
            for box in _text_boxes(child):
                lines.extend(_block_lines(box))
        elif tag == _W_TBL:
            for row in _children(child, _W_TR):
                cells = [" ".join(_block_lines(cell)).strip() for cell in _children(row, _W_TC)]
                cells = [c for c in cells if c]
                if cells:
                    lines.append(" | ".join(cells))
        elif tag == _W_SDT:
            content = child.find(_W_SDT_CONTENT)
            if content is not None:
                lines.extend(_block_lines(content))
    return lines


def _header_footer_lines(section, which: str) -> list[str]:
    parts = []
    if which == "header":
        if section.different_first_page_header_footer:
            parts.append(section.first_page_header)
        parts.append(section.header)
    else:
        parts.append(section.footer)
        if section.different_first_page_header_footer:
            parts.append(section.first_page_footer)
    lines: list[str] = []
    for part in parts:
        try:
            if part.is_linked_to_previous:  # the first section's "linked" = none at all
                continue
            lines.extend(_block_lines(part._element))
        except Exception:  # noqa: BLE001 — a damaged header must not cost the body
            continue
    return lines


def docx_text(data: bytes) -> str:
    """A Word file's text in reading order: the first section's header (where many
    résumés print the name), the body, then the footer. The letterhead line matters to
    redaction — it is where the candidate's name is read from."""
    from docx import Document

    doc = Document(io.BytesIO(data))
    section = doc.sections[0] if len(doc.sections) else None
    lines: list[str] = []
    if section is not None:
        lines.extend(_header_footer_lines(section, "header"))
    lines.extend(_block_lines(doc.element.body))
    if section is not None:
        lines.extend(_header_footer_lines(section, "footer"))
    return "\n".join(lines)


def _docx_pages(data: bytes) -> int:
    """The page count Word saved in docProps/app.xml (0 when it is absent) — what lets
    the too-many-pages check see a merged packet in a Word file too."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("docProps/app.xml")
    except Exception:  # noqa: BLE001
        return 0
    m = re.search(rb"<(?:\w+:)?Pages>\s*(\d+)\s*<", xml)
    return int(m.group(1)) if m else 0


def _docx_images(data: bytes) -> int:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return sum(1 for n in z.namelist() if n.startswith("word/media/"))
    except Exception:  # noqa: BLE001
        return 0


def read_docx(data: bytes, threshold: int) -> LocalRead:
    text = docx_text(data).strip()
    n = _alnum(text)
    images = _docx_images(data)
    pages = _docx_pages(data)
    quality = text_quality(text)
    if n < threshold:
        route = "image-only" if images else "no-text-layer"
    elif quality is not None and quality < MIN_LOCAL_QUALITY:
        route = "garbled-text"
    else:
        route = LOCAL
    stats = {"page_count": pages, "chars": n, "images": images}
    return LocalRead(text if route == LOCAL else "", pages, route, stats,
                     quality if route == LOCAL else None)


# --- entry point ----------------------------------------------------------------------

def extract_local(filename: str, data: bytes, threshold: int) -> LocalRead:
    """Read a résumé here if it carries its own text; otherwise say why it can't be."""
    lower = filename.lower()
    try:
        if lower.endswith(".pdf") or data[:5] == b"%PDF-":
            return read_pdf(data, threshold)
        if lower.endswith(".docx"):
            return read_docx(data, threshold)
    except Exception as exc:  # noqa: BLE001 — unreadable here ≠ unreadable; DI may manage
        log.info("Local extraction could not read a file (%s) — sending it to "
                 "Document Intelligence", type(exc).__name__)
        return LocalRead("", 0, "unreadable", {"error": type(exc).__name__})
    return LocalRead("", 0, "unsupported", {})


# --- fixtures (tests, the doctor's self-test and the A/B script) ------------------------

def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def tiny_pdf(pages: list[list[str] | None]) -> bytes:
    """A minimal PDF: each page is a list of text lines pypdf reads back, `[]` for a
    blank page, or None for an IMAGE-ONLY page (a 1×1 picture and no text — what a
    scan looks like to a text extractor)."""
    objs: list[bytes] = [b"", b""]  # reserve 1 = catalog, 2 = pages

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    kids: list[int] = []
    for lines in pages:
        if lines is None:
            image = add(b"<</Type /XObject /Subtype /Image /Width 1 /Height 1 "
                        b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 3>>\n"
                        b"stream\n\xff\x00\x00\nendstream")
            content = b"q 400 0 0 500 100 150 cm /Im1 Do Q"
            resources = b"<</XObject <</Im1 %d 0 R>> >>" % image
        else:
            content = ("BT /F1 11 Tf 72 740 Td " + " ".join(
                f"({_pdf_escape(line)}) Tj 0 -14 Td" for line in lines) + " ET"
            ).encode("cp1252", errors="replace")
            resources = (b"<</Font <</F1 <</Type /Font /Subtype /Type1 /BaseFont "
                         b"/Helvetica /Encoding /WinAnsiEncoding>> >> >>")
        stream = add(b"<</Length %d>>\nstream\n%s\nendstream" % (len(content), content))
        kids.append(add(b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        b"/Contents %d 0 R /Resources %s>>" % (stream, resources)))
    objs[0] = b"<</Type /Catalog /Pages 2 0 R>>"
    objs[1] = b"<</Type /Pages /Kids [%s] /Count %d>>" % (
        " ".join(f"{k} 0 R" for k in kids).encode(), len(kids))

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<</Size {len(objs) + 1} /Root 1 0 R>>\n"
            f"startxref\n{xref}\n%%EOF").encode()
    return bytes(out)


def tiny_text_pdf(lines: list[str]) -> bytes:
    return tiny_pdf([lines])


def tiny_image_pdf(pages: int = 1) -> bytes:
    return tiny_pdf([None] * pages)


_SELF_TEST_LINES = [
    "Senior Accountant with eight years of general ledger and month-end close work",
    "Prepared statutory financial statements under Ind AS and the Companies Act",
    "Led GST and TDS reconciliations for a shared-services finance team",
    "Implemented an Oracle EBS accounts payable rollout across three entities",
]


def _self_test_docx() -> bytes:
    from docx import Document

    doc = Document()
    for line in _SELF_TEST_LINES:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def preflight(cfg: "AppConfig") -> list["Check"]:
    """What `screener check` prints under "Extraction": the mode in force and where it
    came from, the local readers, and an offline self-test that a text PDF and a Word
    file are read here while a scan would go to Document Intelligence. Network-free."""
    from importlib.metadata import PackageNotFoundError, version

    from resume_screener.config import effective_extraction
    from resume_screener.modes import Check

    ext, source, problem = effective_extraction(cfg)
    checks: list[Check] = []
    if ext.mode == "hybrid":
        what = ("text-bearing PDF and Word résumés are read here and redacted before any "
                "Azure AI service; only files with no readable text go to Document "
                "Intelligence")
    else:
        what = "every résumé goes to Document Intelligence whole, before redaction"
    checks.append(Check("Mode", problem is None,
                        problem or f"{ext.mode} (from {source}) — {what}"))

    def ver(pkg: str) -> str:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return "missing"

    checks.append(Check("Local readers", None,
                        f"pypdf {ver('pypdf')} · python-docx {ver('python-docx')} · a PDF "
                        f"page counts as text from {ext.local_min_chars_per_page} letters"))
    try:
        threshold = ext.local_min_chars_per_page
        got = (extract_local("t.pdf", tiny_text_pdf(_SELF_TEST_LINES), threshold).route,
               extract_local("t.docx", _self_test_docx(), threshold).route,
               extract_local("s.pdf", tiny_image_pdf(), threshold).route)
        ok = got == (LOCAL, LOCAL, "image-only")
        checks.append(Check("Self-test", ok,
                            "text PDF → read here · Word → read here · scan → Document "
                            "Intelligence" if ok else f"unexpected routes {got}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("Self-test", False, f"{type(exc).__name__}: {exc}"))
    return checks
