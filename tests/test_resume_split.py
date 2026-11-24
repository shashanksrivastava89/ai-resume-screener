"""Multi-résumé file splitting (temporary iCIMS bridge).

The primary path is deterministic: parse the iCIMS "Bulk Print" Table of Contents,
then locate each candidate by name and slice their page span into its own PDF.
Conservative default: a file without the TOC signature is left as one candidate.
Offline throughout — PDFs are built in-test (never real candidate data, §6.12);
the LLM fallback never fires because the TOC fixtures map cleanly.
"""

from __future__ import annotations

from pathlib import Path

from resume_screener import resume_split
from resume_screener.ats.local_folder import batch_files


# --- a minimal text-bearing PDF builder (no reportlab dependency) -----------------


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """Build a minimal multi-page PDF; each page is a list of text lines that
    pypdf.extract_text() can read back."""
    objs: list[bytes] = [b"", b""]  # reserve #1 catalog, #2 pages
    kids: list[int] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    for lines in pages:
        content = "BT /F1 12 Tf 72 720 Td " + " ".join(
            f"({_esc(line)}) Tj 0 -16 Td" for line in lines) + " ET"
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


def _bulk_pdf() -> bytes:
    """A 3-candidate iCIMS Bulk Print: TOC page, then Alice(1p), Bob(2p), Carol(1p)."""
    return make_pdf([
        ["Bulk Print Summary", "Table of Contents",
         "1. Alice Alpha - Resume", "2. Bob Beta - Resume", "3. Carol Gamma - Resume"],
        ["Alice Alpha", "Senior Accountant", "alice@example.com", "GL and R2R"],
        ["Bob Beta", "Analyst", "bob@example.com", "AP and AR"],
        ["Bob Beta (continued)", "SAP, Blackline"],
        ["Carol Gamma", "Controller", "carol@example.com", "Audit"],
    ])


def _single_pdf() -> bytes:
    return make_pdf([["Dan Delta", "Accountant", "dan@example.com", "Bookkeeping"]])


# --- detection --------------------------------------------------------------------


def test_toc_detects_three_candidates_with_correct_spans():
    segs = resume_split.detect_boundaries(_bulk_pdf())
    assert [s.label for s in segs] == ["Alice Alpha", "Bob Beta", "Carol Gamma"]
    # TOC on page 0; Alice=1, Bob=2-3 (two pages), Carol=4.
    assert (segs[0].start, segs[0].end) == (1, 1)
    assert (segs[1].start, segs[1].end) == (2, 3)
    assert (segs[2].start, segs[2].end) == (4, 4)


def test_single_resume_is_not_split():
    segs = resume_split.detect_boundaries(_single_pdf())
    assert len(segs) == 1  # no TOC signature -> treated as one résumé


def test_split_is_resilient_to_unlocatable_names():
    """The real failure mode: pypdf can't pinpoint every name in a 50-résumé file.
    As long as a majority anchor, the rest are interpolated so the file still splits
    into len(TOC) résumés — it must NOT collapse back to one candidate."""
    names = [f"{f} {l}" for f, l in zip(
        "Aarav Vivaan Aditya Vihaan Arjun Sai Reyansh Krishna Ishaan Geetika".split(),
        "Sharma Branaola Patel Kumar Singh Gupta Atchison Lillis Rao Das".split())]
    pages = [["Bulk Print Summary", "Table of Contents"]
             + [f"{i + 1}. {n} - Resume" for i, n in enumerate(names)]]
    for i, n in enumerate(names):
        # every third candidate's name is missing from its page (unreadable header)
        first = ["Senior Accountant", "GL R2R"] if i % 3 == 0 else [n, "Senior Accountant"]
        pages.append(first)
        pages.append(["continued experience", "SAP Blackline"])  # a continuation page each
    segs = resume_split.detect_boundaries(make_pdf(pages))
    assert len(segs) == len(names)  # interpolated, not collapsed to 1
    assert [s.label for s in segs] == names
    assert all(s.start <= s.end for s in segs)
    assert [s.start for s in segs] == sorted(s.start for s in segs)  # monotonic


def test_toc_parse_tolerates_jammed_entries():
    """pypdf sometimes runs TOC entries onto one line; finditer must still split them."""
    blob = ("Bulk Print Summary Table of Contents "
            "1. Alice Alpha - Resume 2. Bob Beta - Resume 3. Carol Gamma - Resume")
    parsed = resume_split._parse_toc([blob])
    assert parsed is not None
    names, _ = parsed
    assert names == ["Alice Alpha", "Bob Beta", "Carol Gamma"]


def test_looks_like_bulk_print():
    assert resume_split.looks_like_bulk_print(
        "Bulk Print Summary\nTable of Contents\n1. A One - Resume\n2. B Two - Resume")
    # a normal résumé that merely contains a numbered list is NOT a bulk file
    assert not resume_split.looks_like_bulk_print(
        "Jane Roe\nSenior Accountant\n1. Led the month-end close\n2. Owned reconciliations")


# --- splitting a batch folder -----------------------------------------------------


def test_split_batch_folder_produces_derived_files_and_manifest(tmp_path):
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk_print_output.pdf").write_bytes(_bulk_pdf())
    (folder / "dan-delta.pdf").write_bytes(_single_pdf())

    results = resume_split.split_batch_folder(folder)
    assert len(results) == 1 and results[0].count == 3 and results[0].method == "icims_toc"

    names = [p.name for p in batch_files(folder)]
    # three derived résumés + the untouched single résumé; the bulk original is gone
    assert "dan-delta.pdf" in names
    assert "bulk_print_output.pdf" not in names
    assert sum(1 for n in names if n.endswith(".pdf")) == 4

    groups = resume_split.batch_groups(folder)
    assert len(groups) == 1
    assert groups[0]["source"] == "bulk_print_output.pdf"
    assert groups[0]["count"] == 3
    # the original is preserved out of the way, not enumerated for screening
    assert (folder / resume_split.SOURCES_DIRNAME / "bulk_print_output.pdf").exists()


def test_split_is_idempotent(tmp_path):
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk.pdf").write_bytes(_bulk_pdf())
    resume_split.split_batch_folder(folder)
    before = {p.name for p in batch_files(folder)}
    assert resume_split.split_batch_folder(folder) == []  # nothing new to split
    assert {p.name for p in batch_files(folder)} == before  # derived files untouched


def test_reupload_of_split_bulk_is_dropped(tmp_path):
    folder = tmp_path / "resumes"
    folder.mkdir()
    data = _bulk_pdf()
    (folder / "bulk.pdf").write_bytes(data)
    resume_split.split_batch_folder(folder)
    derived_after_first = sum(1 for p in batch_files(folder))

    # the recruiter re-uploads the same bulk file under a new name
    (folder / "bulk-again.pdf").write_bytes(data)
    resume_split.split_batch_folder(folder)
    # it's recognized as an already-split source and dropped — no duplicate résumés
    assert sum(1 for p in batch_files(folder)) == derived_after_first
    assert "bulk-again.pdf" not in {p.name for p in batch_files(folder)}


def test_cleanup_after_removing_derived(tmp_path):
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "bulk.pdf").write_bytes(_bulk_pdf())
    resume_split.split_batch_folder(folder)
    for p in batch_files(folder):
        p.unlink()  # recruiter removes every derived résumé
    resume_split.cleanup_removed(folder)
    assert resume_split.read_manifest(folder) == {}
    assert resume_split.batch_groups(folder) == []
    assert not (folder / resume_split.SOURCES_DIRNAME).exists()  # stored original + dir gone


# --- wired through the batch upload route ------------------------------------------

import shutil  # noqa: E402

import pytest  # noqa: E402

from tests.test_sso_identity import _as  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")  # multi_resume_split default: true
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app
    return TestClient(app)


def _mk_role(client) -> str:
    r = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "GL Accountant", "jd_text": "Owns the GL close.",
        "job_location": "IN", "icims_req_id": "2026-7788"})
    assert r.status_code == 200, r.text
    return r.json()["req_id"]


def _upload(client, req_id, name, data):
    return client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"),
                       files=[("files", (name, data, "application/pdf"))])


def test_upload_route_splits_bulk_file(client):
    req_id = _mk_role(client)
    r = _upload(client, req_id, "bulk_print_output.pdf", _bulk_pdf())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3  # three derived résumés are the screenable candidates
    assert len(body["groups"]) == 1 and body["groups"][0]["count"] == 3
    assert "split into 3 résumés" in body["sentence"]


def test_upload_route_flag_off_keeps_single_file(client, monkeypatch):
    monkeypatch.setattr("resume_screener.feature_flags.flag_enabled", lambda *a, **k: False)
    req_id = _mk_role(client)
    r = _upload(client, req_id, "bulk_print_output.pdf", _bulk_pdf())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1  # untouched — exactly the pre-feature behaviour
    assert body["groups"] == []
    assert body["files"][0]["name"] == "bulk_print_output.pdf"


# --- identity comes from the WRITTEN slice, never the parent's page text -------------


def test_slice_identity_is_read_from_the_written_file_not_the_parent_text(monkeypatch, tmp_path):
    """A performance shortcut that silently hid two real candidates (2026-08-26).

    Splitting is dominated by PDF text extraction, and _apply_split re-parses every
    slice it has just written to get that person's email/phone. The parent document's
    per-page text is already in hand during detection, so deriving each slice's tokens
    from `page_texts[start:end+1]` looked free — it halved the cost of a split.

    It is WRONG. The parent's text for pages 88-90 is not the text of the file written
    from pages 88-90: pypdf's page copy drops some content, and the parent extraction
    picks up neighbouring text the slice does not contain. On a real 6-packet batch that
    gave one candidate a phone number belonging to the person in the ADJACENT segment,
    and matched an oversized 19-page slice against someone in a different packet — 2 of
    232 people dropped as duplicates they were not.

    The written slice IS the candidate's file of record, so identity must be read from
    it. This test pins the contract by asserting the reader is handed the SLICE bytes,
    not the parent's text: dedupe must never be able to hide a person on the strength of
    text that is not in their file (guardrail §6.3 — no hiding).
    """
    from resume_screener import contacts, resume_split

    seen_payloads: list[bytes] = []

    def spy(data: bytes, suffix: str):
        seen_payloads.append(data)
        return set()

    # _apply_split imports contact_tokens INSIDE the function, so patching the module
    # attribute is what the call actually resolves.
    monkeypatch.setattr(contacts, "contact_tokens", spy)

    folder = tmp_path / "batch"
    folder.mkdir()
    src = folder / "bulk.pdf"
    data = _bulk_pdf()
    src.write_bytes(data)

    resume_split.split_batch_folder(folder)

    assert seen_payloads, "contact_tokens was not consulted for the written slices"
    parent = data
    for payload in seen_payloads:
        # Each payload must be a SLICE — smaller than the parent and not the parent
        # itself. If someone reintroduces the shortcut, tokens come from text rather
        # than bytes and this loop stops seeing slice PDFs at all.
        assert payload != parent, "identity was read from the parent, not the slice"
        assert payload.startswith(b"%PDF"), "identity must be read from PDF BYTES"


# --- the LLM fallback's prompt carries no identities (2026-10-14) ----------------------
#
# When the table of contents' names cannot be found on the pages, the model is asked
# where each résumé starts. It used to be sent each page's opening characters and the
# names as they were. Synthetic people only.

_FALLBACK_PAGES = [
    "Bulk Print Summary\nTable of Contents\n1. Alice Alpha - Resume\n2. Bob Beta - Resume",
    "ALICE ALPHA\nEmail: alice.alpha@example.com\nPhone: +91 98765 43210\n"
    "Senior Accountant\nGL and R2R close",
    "Continued experience\nOracle EBS rollout across three entities",
    "Robert Beta\nbob.beta@example.com | 98765 12345\nAnalyst, AP and AR",
]
_FALLBACK_NAMES = ["Alice Alpha", "Bob Beta"]


def _assert_no_identity(prompt: str) -> None:
    low = prompt.casefold()
    for name_part in ("alice", "alpha", "bob", "beta", "robert"):
        assert name_part not in low, name_part
    assert "@" not in prompt and "98765" not in prompt
    assert "[CANDIDATE]" in prompt


def test_the_fallback_prompt_withholds_names_and_contacts():
    from resume_screener.resume_split import _boundary_prompt

    prompt = _boundary_prompt(_FALLBACK_PAGES, _FALLBACK_NAMES)
    _assert_no_identity(prompt)
    # what the model needs to find the boundaries is still there
    assert "2 candidates" in prompt and "[page 4]" in prompt
    assert "Senior Accountant" in prompt and "Continued experience" in prompt


def test_the_fallback_sends_only_the_withheld_prompt_and_still_splits(monkeypatch):
    from types import SimpleNamespace

    from resume_screener import azure_clients, config
    from resume_screener import resume_split as rs

    sent: list[list[dict]] = []

    def fake_parse(client, model, messages, schema):
        sent.append(messages)
        return schema(start_pages=[2, 4])

    monkeypatch.setattr(config, "load_settings",
                        lambda: SimpleNamespace(azure_openai_chat_deployment="chat"))
    monkeypatch.setattr(config, "api_keys_for", lambda settings: None)
    monkeypatch.setattr(azure_clients, "get_openai_client",
                        lambda settings, keys=None: SimpleNamespace(client=object()))
    monkeypatch.setattr(azure_clients, "parse_structured", fake_parse)

    segments = rs._llm_boundaries(_FALLBACK_PAGES, _FALLBACK_NAMES)
    assert [(s.label, s.start, s.end) for s in segments] == [
        ("Alice Alpha", 1, 2), ("Bob Beta", 3, 3)]
    [messages] = sent
    _assert_no_identity(" ".join(m["content"] for m in messages))


def test_a_continuation_page_heading_is_not_mistaken_for_a_new_letterhead():
    """Résumé redaction reads any short first line as a name. In the fallback that
    would put "[CANDIDATE]" — the model's cue for "a new résumé starts here" — at the
    top of every page that opens with a section heading."""
    from resume_screener.resume_split import _boundary_prompt

    prompt = _boundary_prompt(_FALLBACK_PAGES, _FALLBACK_NAMES)
    lines = {line.split("]", 1)[0] + "]": line for line in prompt.splitlines()
             if line.startswith("[page ")}
    assert lines["[page 2]"].startswith("[page 2] [CANDIDATE]")   # a real letterhead
    assert lines["[page 4]"].startswith("[page 4] [CANDIDATE]")
    assert lines["[page 3]"].startswith("[page 3] Continued experience")
    assert lines["[page 1]"].startswith("[page 1] Bulk Print Summary Table of Contents")
