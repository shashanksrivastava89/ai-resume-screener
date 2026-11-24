"""Active content out of stored and served PDFs (2026-10-16, pen-test RS-04).

The pen test uploaded a résumé whose JavaScript ran in the reviewer's browser. These
build hostile PDFs object by object — every place a PDF can carry an action — and pin
that pdfsafe removes them, keeps ordinary links, leaves clean files byte-identical,
and that intake and both download routes only ever hand out clean bytes.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from resume_screener import pdfsafe
from tests.test_resume_split import make_pdf


def _doc(*, catalog_extra: bytes = b"", page_extra: bytes = b"",
         annots: tuple[bytes, ...] = (), extra: tuple[bytes, ...] = ()) -> bytes:
    """A one-page PDF. Objects: 1 catalog, 2 pages, 3 content, 4 page, then `extra`
    from 5 in order, then `annots` (so /Annots exercises indirect references)."""
    content = b"BT /F1 12 Tf 72 720 Td (Jane Doe - Accountant) Tj ET"
    first_annot = 5 + len(extra)
    annots_ref = (b"/Annots [" + b" ".join(b"%d 0 R" % (first_annot + i)
                                         for i in range(len(annots))) + b"]") if annots else b""
    objs = [
        b"<</Type /Catalog /Pages 2 0 R " + catalog_extra + b">>",
        b"<</Type /Pages /Kids [4 0 R] /Count 1>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(content), content),
        (b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R "
         b"/Resources <</Font <</F1 <</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> >> >> "
         + annots_ref + b" " + page_extra + b">>"),
        *extra, *annots,
    ]
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


def _stream(body: bytes) -> bytes:
    return b"<</Length %d>>\nstream\n%s\nendstream" % (len(body), body)


JS = b"<</S /JavaScript /JS (app.alert(1))>>"

CASES = {
    "open action": dict(catalog_extra=b"/OpenAction " + JS),
    "open action, indirect, script in a stream": dict(
        catalog_extra=b"/OpenAction 5 0 R",
        extra=(b"<</S /JavaScript /JS 6 0 R>>", _stream(b"app.alert(2)"))),
    "document action": dict(catalog_extra=b"/AA <</WC " + JS + b">>"),
    "document JavaScript name tree": dict(
        catalog_extra=b"/Names <</JavaScript 5 0 R>>",
        extra=(b"<</Kids [6 0 R]>>", b"<</Names [(x) 7 0 R] /Limits [(x) (x)]>>", JS)),
    "form": dict(
        catalog_extra=b"/AcroForm <</Fields [5 0 R] /XFA (xfa)>>",
        extra=(b"<</FT /Tx /T (name) /AA <</K " + JS + b">> >>",)),
    "page action": dict(page_extra=b"/AA <</O " + JS + b">>"),
    "link that runs a script": dict(
        annots=(b"<</Type /Annot /Subtype /Link /Rect [0 0 9 9] /A " + JS + b">>",)),
    "field widget action": dict(
        annots=(b"<</Type /Annot /Subtype /Widget /Rect [0 0 9 9] /AA <</E " + JS + b">> >>",)),
}


@pytest.mark.parametrize("name", list(CASES))
def test_every_place_a_script_can_hide_is_cleaned(name):
    hostile = _doc(**CASES[name])
    assert pdfsafe.inspect(hostile), name
    clean, removed = pdfsafe.strip(hostile)
    assert removed and clean is not hostile
    assert b"alert" not in clean, name
    root = PdfReader(io.BytesIO(clean)).trailer["/Root"]
    for key in ("/OpenAction", "/AA", "/Names"):
        assert key not in root, (name, key)
    assert pdfsafe.inspect(clean) == []  # and the result is clean by its own measure


def test_a_bookmark_that_launches_a_program_is_cut():
    hostile = _doc(catalog_extra=b"/Outlines 5 0 R", extra=(
        b"<</Type /Outlines /First 6 0 R /Last 6 0 R /Count 1>>",
        b"<</Title (Open) /Parent 5 0 R /A <</S /Launch /F (calc.exe)>> >>"))
    clean, removed = pdfsafe.strip(hostile)
    assert removed and b"calc" not in clean and b"/Launch" not in clean


def test_embedded_files_are_removed():
    hostile = _doc(catalog_extra=b"/Names <</EmbeddedFiles <</Names [(evil.exe) 5 0 R]>> >>",
                   extra=(b"<</Type /Filespec /F (evil.exe) /EF <</F 6 0 R>> >>",
                          _stream(b"MZ-not-a-resume")))
    clean, removed = pdfsafe.strip(hostile)
    assert removed and b"evil" not in clean and b"MZ-not-a-resume" not in clean


def test_ordinary_links_survive_and_only_the_script_in_a_chain_goes():
    link = b"<</Type /Annot /Subtype /Link /Rect [0 0 9 9] /A <</S /URI /URI (%s)%s>> >>"
    hostile = _doc(annots=(
        link % (b"https://example.com", b""),
        link % (b"https://example.org", b" /Next " + JS),
        link % (b"https://example.net", b" /Next [<</S /GoTo /D [4 0 R /Fit]>> " + JS + b"]"),
        b"<</Type /Annot /Subtype /FileAttachment /Rect [0 0 9 9] /FS <</F (x.exe)>> >>",
    ))
    clean, removed = pdfsafe.strip(hostile)
    assert removed and b"alert" not in clean and b"/FileAttachment" not in clean
    page = PdfReader(io.BytesIO(clean)).pages[0]
    actions = [a.get_object()["/A"] for a in page["/Annots"]]
    # pypdf writes strings escaped, so read the links back rather than grep for them
    assert [a["/URI"] for a in actions] == [
        "https://example.com", "https://example.org", "https://example.net"]
    assert "/Next" not in actions[1] and "/Next" not in actions[2]


def test_a_clean_file_comes_back_as_the_same_bytes():
    plain = make_pdf([["Jane Doe", "Accountant"]])
    clean, removed = pdfsafe.strip(plain)
    assert clean is plain and removed == []
    with_dest = _doc(catalog_extra=b"/OpenAction [4 0 R /Fit]")  # LaTeX does this
    assert pdfsafe.strip(with_dest)[0] is with_dest


def test_not_a_pdf_and_unreadable_files_pass_through():
    for data in (b"hello", b"pdf-lock-0", b"%PDF-1.4 truncated"):
        assert pdfsafe.strip(data)[0] is data and pdfsafe.inspect(data) == []
    assert pdfsafe.unreadable_but_marked(b"%PDF-1.4 /JavaScript (x)")
    assert not pdfsafe.unreadable_but_marked(b"%PDF-1.4 /OpenAction")


def test_an_encrypted_file_that_opens_without_a_password_is_cleaned():
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.add_js("app.alert(13)")
    w.encrypt(user_password="", owner_password="owner")
    buf = io.BytesIO()
    w.write(buf)
    clean, removed = pdfsafe.strip(buf.getvalue())
    assert removed
    reader = PdfReader(io.BytesIO(clean))
    assert not reader.is_encrypted and "/Names" not in reader.trailer["/Root"]


def test_the_rewrite_is_deterministic():
    hostile = _doc(catalog_extra=b"/OpenAction " + JS)
    assert pdfsafe.strip(hostile)[0] == pdfsafe.strip(hostile)[0]


def test_a_served_file_is_checked_once_per_state(tmp_path):
    path = tmp_path / "cv.pdf"
    path.write_bytes(_doc(catalog_extra=b"/OpenAction " + JS))
    first = pdfsafe.checked_file(path)
    assert first.sanitized and b"alert" not in first.sanitized
    assert pdfsafe.checked_file(path) is first
    path.write_bytes(make_pdf([["Clean now"]]))
    assert pdfsafe.checked_file(path).sanitized is None
    path.write_bytes(b"%PDF-1.4 /JavaScript (x)")
    assert pdfsafe.checked_file(path).attachment_only


# pypdf writes strings escaped ("app\056alert"), so the checks above look for words it
# never escapes ("alert", "calc", "evil") — a check for "app.alert" would pass vacuously.
HOSTILE = _doc(catalog_extra=b"/OpenAction " + JS)


# --- intake, serving, adoption, the sweep ----------------------------------------------

import json  # noqa: E402

from tests.test_batch_download import _mk_role, _upload, client, workspace  # noqa: E402,F401
from tests.test_sso_identity import _as  # noqa: E402


def _batch(req_id: str) -> Path:
    return Path("data/roles") / req_id / "resumes"


def test_an_uploaded_pdf_is_stored_clean_and_its_id_is_the_clean_files(client):
    from resume_screener.api import service
    from resume_screener.events import load_events

    req_id = _mk_role(client)
    _upload(client, req_id, "cv.pdf", HOSTILE)
    stored = _batch(req_id) / "cv.pdf"
    data = stored.read_bytes()
    assert b"alert" not in data and pdfsafe.inspect(data) == []
    assert service.cached_candidate_id(stored) == service.candidate_id_for(data)
    index = json.loads((_batch(req_id) / ".contact_index.json").read_text(encoding="utf-8"))
    assert index["files"]["cv.pdf"]["hash"] == service.candidate_id_for(data)
    served = client.get(f"/api/roles/{req_id}/batch/file/cv.pdf?inline=1", headers=_as("TASpecialist"))
    assert served.status_code == 200 and b"alert" not in served.content
    [event] = load_events(req_id=req_id, kinds=["pdf_active_content_removed"])
    assert event["data"]["file"] == "cv.pdf" and "Jane" not in json.dumps(event)


def test_a_file_stored_before_cleaning_existed_is_served_clean(client):
    req_id = _mk_role(client)
    folder = _batch(req_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.pdf").write_bytes(HOSTILE)
    (folder / "odd.pdf").write_bytes(b"%PDF-1.4 /JavaScript (app.alert(1))")
    private = Path("output") / req_id / "private"
    private.mkdir(parents=True)
    (private / "candidate_map.json").write_text(json.dumps(
        {"c-old": str((folder / "old.pdf").resolve()), "c-odd": str((folder / "odd.pdf").resolve())}),
        encoding="utf-8")
    ta = _as("TASpecialist")
    for inline in ("1", "0"):
        r = client.get(f"/api/roles/{req_id}/candidates/c-old/resume?inline={inline}", headers=ta)
        assert r.status_code == 200 and b"alert" not in r.content
        assert r.headers["content-disposition"].startswith("inline" if inline == "1" else "attachment")
    # unreadable but naming JavaScript: never rendered in the browser
    odd = client.get(f"/api/roles/{req_id}/candidates/c-odd/resume?inline=1", headers=ta)
    assert odd.headers["content-disposition"].startswith("attachment")
    zipped = client.get(f"/api/roles/{req_id}/batch/download", headers=ta)
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zipped.content)) as zf:
        assert b"alert" not in zf.read("old.pdf")


def test_a_pdf_dropped_into_a_batch_by_hand_is_cleaned_when_adopted(tmp_path, monkeypatch):
    from resume_screener import batch_prep
    from resume_screener.ats import local_folder

    monkeypatch.setenv("BATCH_PREP_INLINE", "0")
    monkeypatch.setattr(batch_prep, "_emit", lambda *a, **k: None)
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "byhand.pdf").write_bytes(HOSTILE)
    batch_prep.stage_bytes("new.pdf", make_pdf([["Someone Else"]]), folder)
    assert batch_prep.run_worker("role-x", folder) == 0
    data = (folder / "byhand.pdf").read_bytes()
    assert b"alert" not in data
    index = json.loads((folder / ".contact_index.json").read_text(encoding="utf-8"))
    assert index["files"]["byhand.pdf"]["hash"] == local_folder.candidate_id_for(data)


def test_the_sweep_reports_first_and_cleans_on_fix(workspace):
    from typer.testing import CliRunner

    from resume_screener.cli import app as cli
    from resume_screener.events import load_events

    folder = Path("data/roles/finance-analyst/resumes")
    folder.mkdir(parents=True)
    (folder / "old.pdf").write_bytes(HOSTILE)
    (folder / "fine.pdf").write_bytes(make_pdf([["Clean"]]))
    report = CliRunner().invoke(cli, ["data", "sanitize-pdfs"])
    assert report.exit_code == 0, report.output
    assert "1 of 2 stored PDF(s) carry active content" in report.output
    assert (folder / "old.pdf").read_bytes() == HOSTILE  # nothing rewritten
    fixed = CliRunner().invoke(cli, ["data", "sanitize-pdfs", "--fix"])
    assert fixed.exit_code == 0 and "Cleaned 1 of 2" in fixed.output
    assert b"alert" not in (folder / "old.pdf").read_bytes()
    [event] = load_events(kinds=["pdf_active_content_removed"])
    assert event["actor_role"] == "cli" and event["req_id"] == "finance-analyst"
