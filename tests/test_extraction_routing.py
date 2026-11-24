"""Local-first extraction (InfoSec question 6, 2026-10-14). Offline, synthetic files only.

A résumé that carries its own text is read on this machine and redacted before any
Azure AI service sees it; only a file with no usable text goes to Document
Intelligence. These pin the routing, the cache isolation between modes, the Word
reader's coverage (header, tables, text boxes — each once, in reading order), and
that locally-read text redacts exactly as the redaction suite expects.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from azure.core.exceptions import HttpResponseError

from resume_screener import localextract
from resume_screener.cache import JsonCache, content_hash
from resume_screener.config import AppConfig, ExtractionConfig, effective_extraction, load_app_config
from resume_screener.ingest import extract_file, extraction_summary
from resume_screener.localextract import tiny_pdf
from resume_screener.redact import redact_text

MODEL = "prebuilt-layout"
HYBRID = ExtractionConfig(mode="hybrid", local_min_chars_per_page=200)
DI_ONLY = ExtractionConfig(mode="di-only", local_min_chars_per_page=200)

FULL_PAGE = [
    "Senior Accountant with eight years of general ledger and month-end close work",
    "Prepared statutory financial statements under Ind AS and the Companies Act",
    "Led GST and TDS reconciliations for a shared-services finance team",
    "Implemented an Oracle EBS accounts payable rollout across three entities",
]
DECLARATION = ["Declaration: the above is true to the best of my knowledge."]


class FakeDI:
    """Records every file it is sent; answers like prebuilt-layout."""

    def __init__(self, text="TEXT READ BY DOCUMENT INTELLIGENCE", confidence=0.97):
        self.calls: list[tuple[str, int]] = []
        self.text, self.confidence = text, confidence

    def begin_analyze_document(self, model_id, file_bytes, content_type=None):
        self.calls.append((model_id, len(file_bytes)))
        word = SimpleNamespace(confidence=self.confidence)
        result = SimpleNamespace(content=self.text, pages=[SimpleNamespace(words=[word])])
        return SimpleNamespace(result=lambda: result)


class RejectingDI(FakeDI):
    def begin_analyze_document(self, model_id, file_bytes, content_type=None):
        self.calls.append((model_id, len(file_bytes)))
        exc = HttpResponseError(message="unsupported file")
        exc.status_code = 400
        raise exc


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _extract(tmp_path, name, data, di, mode=HYBRID):
    return extract_file(di, MODEL, _write(tmp_path, name, data), JsonCache(tmp_path / ".cache"), mode)


def _docx(paragraphs=(), header=None, table=None, text_box=None, picture=False) -> bytes:
    from docx import Document
    from docx.oxml import parse_xml

    doc = Document()
    if header:
        doc.sections[0].header.paragraphs[0].text = header
    for line in paragraphs:
        doc.add_paragraph(line)
    if table:
        t = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    if text_box:
        # A modern Word text box: the DrawingML shape plus the VML copy Word writes
        # beside it for older readers. Read naively, its text appears twice.
        doc.element.body.insert(len(doc.element.body) - 1, parse_xml(
            '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
            'xmlns:v="urn:schemas-microsoft-com:vml"><w:r><mc:AlternateContent>'
            f'<mc:Choice Requires="wps"><w:drawing><wps:wsp><wps:txbx><w:txbxContent><w:p><w:r>'
            f'<w:t>{text_box}</w:t></w:r></w:p></w:txbxContent></wps:txbx></wps:wsp></w:drawing>'
            f'</mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r>'
            f'<w:t>{text_box}</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict>'
            '</mc:Fallback></mc:AlternateContent></w:r></w:p>'))
    if picture:
        from PIL import Image

        img = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(img, format="PNG")
        img.seek(0)
        doc.add_picture(img)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- routing ---------------------------------------------------------------------------


def test_a_text_pdf_is_read_here_and_never_sent(tmp_path):
    di = FakeDI()
    res = _extract(tmp_path, "cv.pdf", tiny_pdf([FULL_PAGE]), di)
    assert di.calls == []
    assert (res.extractor, res.route, res.page_count) == ("local", "text", 1)
    assert res.extraction_confidence == 1.0
    assert "Ind AS" in res.text


def test_a_scan_goes_to_document_intelligence(tmp_path):
    di = FakeDI()
    res = _extract(tmp_path, "scan.pdf", localextract.tiny_image_pdf(2), di)
    assert len(di.calls) == 1
    assert (res.extractor, res.route) == ("document-intelligence", "image-only")
    assert res.text == di.text and res.extraction_confidence == 0.97
    assert res.stats["image_only_pages"] == 2


def test_mostly_scanned_pages_send_the_file(tmp_path):
    di = FakeDI()
    res = _extract(tmp_path, "mixed.pdf", tiny_pdf([FULL_PAGE, None, None]), di)
    assert len(di.calls) == 1 and res.route == "image-only"


def test_a_signed_declaration_page_does_not_send_a_text_resume(tmp_path):
    """Many résumés end with a short declaration and a scanned signature. One such
    page beside a full page of text must not route the whole file to OCR."""
    di = FakeDI()
    res = _extract(tmp_path, "cv.pdf", tiny_pdf([FULL_PAGE, None]), di)
    assert di.calls == [] and res.route == "text"
    assert res.stats["text_pages"] == 1 and res.stats["image_only_pages"] == 1


def test_a_file_with_no_text_layer_is_not_scored_empty(tmp_path):
    """Letters drawn as shapes, or a blank export, read as nothing here. Scoring an
    empty read would silently mark the candidate as meeting nothing — DI reads it."""
    di = FakeDI()
    res = _extract(tmp_path, "outlined.pdf", tiny_pdf([[], []]), di)
    assert len(di.calls) == 1 and res.route == "no-text-layer"


def test_di_only_mode_sends_everything_under_the_old_cache_key(tmp_path):
    di = FakeDI()
    data = tiny_pdf([FULL_PAGE])
    res = _extract(tmp_path, "cv.pdf", data, di, DI_ONLY)
    assert len(di.calls) == 1
    assert (res.extractor, res.route) == ("document-intelligence", "di-only")
    # the key entries cached before 2026-10-14 live under — still served
    assert JsonCache(tmp_path / ".cache").get("ingest", content_hash(data, MODEL)) is not None


def test_switching_modes_never_serves_the_other_modes_text(tmp_path):
    di = FakeDI()
    data = tiny_pdf([FULL_PAGE])
    local = _extract(tmp_path, "cv.pdf", data, di, HYBRID)
    remote = _extract(tmp_path, "cv.pdf", data, di, DI_ONLY)
    again = _extract(tmp_path, "cv.pdf", data, di, HYBRID)
    assert "Ind AS" in local.text and remote.text == di.text
    assert again.from_cache and again.text == local.text and again.extractor == "local"
    assert len(di.calls) == 1  # only the di-only read went out


def test_a_scan_read_once_is_not_paid_for_again_after_a_mode_switch(tmp_path):
    di = FakeDI()
    data = localextract.tiny_image_pdf(1)
    _extract(tmp_path, "scan.pdf", data, di, DI_ONLY)
    res = _extract(tmp_path, "scan.pdf", data, di, HYBRID)
    assert len(di.calls) == 1 and res.from_cache and res.route == "image-only"


def test_an_entry_cached_before_this_change_still_reads(tmp_path):
    data = tiny_pdf([FULL_PAGE])
    cache = JsonCache(tmp_path / ".cache")
    cache.set("ingest", content_hash(data, MODEL), {
        "candidate_id": "c1", "source_file": "cv.pdf", "text": "old text", "page_count": 1,
        "extraction_confidence": 0.99, "extractor": "document-intelligence"})
    res = extract_file(FakeDI(), MODEL, _write(tmp_path, "cv.pdf", data), cache, DI_ONLY)
    assert res.from_cache and res.text == "old text" and res.route == "di-only"


# --- Word files ------------------------------------------------------------------------


def test_a_word_resume_is_read_here_in_reading_order(tmp_path):
    di = FakeDI()
    data = _docx(paragraphs=FULL_PAGE, header="Bhavika Dalal — Finance",
                 table=[["SAP FICO", "Tally"], ["GST", "TDS"]],
                 text_box="Languages: English, Hindi")
    res = _extract(tmp_path, "cv.docx", data, di)
    assert di.calls == [] and (res.extractor, res.route) == ("local", "text")
    lines = res.text.splitlines()
    assert lines[0] == "Bhavika Dalal — Finance"     # the letterhead, where names live
    assert "SAP FICO | Tally" in lines and "GST | TDS" in lines
    assert res.text.count("Languages: English, Hindi") == 1  # not twice via the fallback copy


def test_a_word_file_holding_a_scanned_page_goes_to_document_intelligence(tmp_path):
    di = FakeDI()
    res = _extract(tmp_path, "scan.docx", _docx(paragraphs=["CV"], picture=True), di)
    assert len(di.calls) == 1 and res.route == "image-only"


def test_a_word_file_document_intelligence_rejects_still_reads(tmp_path):
    di = RejectingDI()
    res = _extract(tmp_path, "cv.docx", _docx(paragraphs=FULL_PAGE), di, DI_ONLY)
    assert res.extractor == "python-docx" and "Ind AS" in res.text


# --- redaction holds on locally-read text ----------------------------------------------


def test_redaction_is_unchanged_on_a_locally_read_pdf(tmp_path):
    from tests.test_redact import SAMPLE

    res = _extract(tmp_path, "GERARD BRANAOLA.pdf", tiny_pdf([SAMPLE.splitlines()]), FakeDI())
    assert res.extractor == "local"
    out = redact_text(res.text, res.candidate_id, "GERARD BRANAOLA.pdf").redacted_text
    low = out.lower()
    for gone in ("rahul", "branaola", "gmail", "98765", "400058", "15/06/1987", "married",
                 "oisin", "indian", "abcde1234f", "linkedin.com/in"):
        assert gone not in low, gone
    for kept in ("Tata Motors", "Ind AS", "Apr 2018 - Present", "Sharma & Associates",
                 "7500000", "B.Com", "CA, ICAI, 2012", "SAP FICO",
                 "12 years in GST compliance"):
        assert kept in out, kept


@pytest.mark.parametrize("sample, filename, gone, kept", [
    ("SAMPLE_HU", "Kylie Chaaya CV.docx",
     ("kovács", "péter", "1987", "férfi", "házas", "irinyi", "123 456 789", "example.hu"),
     ("MOL Nyrt.", "IFRS", "ÁFA-bevallás", "2018. 04. – jelenleg", "Jogosítvány: B")),
    ("SAMPLE_DE", "Anna Schroeder Lebenslauf.docx",
     ("schröder", "anna", "1985", "verheiratet", "werner-von-siemens"),
     ("Siemens AG", "HGB", "SAP S/4HANA", "Bilanzbuchhalter (IHK)", "Führerschein Klasse B")),
])
def test_redaction_is_unchanged_on_a_locally_read_word_file(tmp_path, sample, filename, gone, kept):
    import tests.test_redact_global as g

    text = getattr(g, sample)
    res = _extract(tmp_path, filename, _docx(paragraphs=text.splitlines()), FakeDI())
    assert res.extractor == "local"
    out = redact_text(res.text, res.candidate_id, filename).redacted_text
    for fragment in gone:
        assert fragment not in out.casefold(), fragment
    for fragment in kept:
        assert fragment in out, fragment


# --- read quality, the run summary, the doctor ----------------------------------------


def test_read_quality_measures_the_text_layer():
    q = localextract.text_quality
    assert q("Clean digital text") == 1.0
    assert q("abcd�") == pytest.approx(0.8)
    assert q("(cid:12)(cid:13)(cid:14) ok") < 0.75
    assert q("   ") is None


def test_the_run_summary_counts_where_text_came_from():
    stats = [
        {"from_cache": False, "extractor": "local", "route": "text", "confidence": 1.0},
        {"from_cache": False, "extractor": "document-intelligence", "route": "image-only",
         "confidence": 0.9},
        {"from_cache": True, "extractor": "document-intelligence", "route": "no-text-layer",
         "confidence": 0.95},
        {"from_cache": False, "extractor": "python-docx", "route": "di-only", "confidence": None},
    ]
    s = extraction_summary(stats, "hybrid")
    assert (s["local_extractions"], s["di_documents"], s["di_calls"]) == (1, 2, 1)
    assert s["routed_to_di"] == 2 and s["cache_hits"] == 1 and s["fallback_extractions"] == 1
    assert s["routes"] == {"di-only": 1, "image-only": 1, "no-text-layer": 1, "text": 1}


def test_the_committed_config_reads_locally_by_default():
    cfg = load_app_config()
    assert (cfg.extraction.mode, cfg.extraction.local_min_chars_per_page) == ("hybrid", 200)
    assert AppConfig().extraction.mode == "hybrid"  # the code default a hosted share gets


def test_the_environment_switch_wins_and_says_so(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MODE", " DI-only ")
    ext, source, problem = effective_extraction(AppConfig())
    assert (ext.mode, source, problem) == ("di-only", "EXTRACTION_MODE", None)
    checks = {c.name: c for c in localextract.preflight(AppConfig())}
    assert checks["Mode"].ok and "di-only (from EXTRACTION_MODE)" in checks["Mode"].detail

    monkeypatch.setenv("EXTRACTION_MODE", "local")
    ext, source, problem = effective_extraction(AppConfig())
    assert ext.mode == "hybrid" and "not one of" in problem
    assert localextract.preflight(AppConfig())[0].ok is False


def test_the_doctor_self_test_passes_offline(monkeypatch):
    monkeypatch.delenv("EXTRACTION_MODE", raising=False)
    checks = {c.name: c for c in localextract.preflight(AppConfig())}
    assert checks["Mode"].ok and "hybrid (from default)" in checks["Mode"].detail
    assert checks["Self-test"].ok, checks["Self-test"].detail
