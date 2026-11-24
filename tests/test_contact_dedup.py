"""Candidate de-duplication by CONTACT identity (2026-07-23).

Two different people can share a name on one requisition — both must be kept.
The same person can submit two slightly different résumés — those must de-dup.
So identity for de-dup is the email/phone read locally from the résumé, never
the name. A résumé with no readable contact falls back to exact content-hash
de-dup. DOCX fixtures are built with python-docx so extraction really runs."""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import add_bytes_to_batch, batch_files
from resume_screener.contacts import contact_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]


def _docx(*lines: str) -> bytes:
    """A tiny real DOCX carrying the given lines (so contact extraction works)."""
    import docx

    doc = docx.Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- the token extractor ------------------------------------------------------------


def test_contact_tokens_reads_email_and_normalizes_phone():
    data = _docx("Jane Doe", "jane.doe@example.com", "+91 98765-43210")
    tokens = contact_tokens(data, ".docx")
    assert "email:jane.doe@example.com" in tokens
    assert "phone:9876543210" in tokens  # country code + formatting stripped


def test_contact_tokens_empty_when_unreadable():
    # not a real docx/pdf -> extraction fails -> no tokens (never raises)
    assert contact_tokens(b"not a real document", ".pdf") == set()
    assert contact_tokens(b"still not", ".docx") == set()


# --- de-dup at add time -------------------------------------------------------------


def test_same_email_is_a_duplicate_even_with_a_different_name(tmp_path):
    batch = tmp_path / "b"
    a = _docx("Anita Rao", "anita@example.com", "9876500000")
    # same person, edited résumé (different bytes, different display name)
    b = _docx("Anita R. (updated)", "anita@example.com", "different content here")
    assert add_bytes_to_batch("anita_v1.docx", a, batch) == "added"
    assert add_bytes_to_batch("anita_v2.docx", b, batch) == "duplicate"
    assert len(batch_files(batch)) == 1


def test_two_people_with_the_SAME_name_are_both_kept(tmp_path):
    batch = tmp_path / "b"
    # namesakes: same name, different email AND phone -> two distinct candidates
    a = _docx("Rahul Sharma", "rahul.s@example.com", "9811111111")
    b = _docx("Rahul Sharma", "rahul.k@example.com", "9822222222")
    assert add_bytes_to_batch("rahul_1.docx", a, batch) == "added"
    assert add_bytes_to_batch("rahul_2.docx", b, batch) == "added"
    assert len(batch_files(batch)) == 2


def test_shared_phone_alone_is_a_duplicate(tmp_path):
    batch = tmp_path / "b"
    a = _docx("Meera Roach", "meera@example.com", "+91 98765 43210")
    b = _docx("M Roach", "meera.roach@work.example.com", "9876543210")  # same number, diff email
    assert add_bytes_to_batch("meera_1.docx", a, batch) == "added"
    assert add_bytes_to_batch("meera_2.docx", b, batch) == "duplicate"


def test_no_readable_contact_falls_back_to_content_hash(tmp_path):
    batch = tmp_path / "b"
    # unreadable "pdf"s -> no tokens -> only exact-content de-dup applies
    assert add_bytes_to_batch("x.pdf", b"%garbage one", batch) == "added"
    assert add_bytes_to_batch("y.pdf", b"%garbage two", batch) == "added"   # different bytes
    assert add_bytes_to_batch("z.pdf", b"%garbage one", batch) == "duplicate"  # identical bytes


# --- through the HTTP upload route --------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)

    from resume_screener.api.app import app

    return TestClient(app)


def test_bulk_upload_dedupes_same_person_keeps_namesakes(client):
    from tests.test_sso_identity import _as

    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash.", "job_location": "IN",
        "icims_req_id": "IN-777",
    })
    req_id = created.json()["req_id"]
    ct = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    files = [
        ("files", ("anita.docx", _docx("Anita Rao", "anita@example.com", "9876500000"), ct)),
        # same person again (different file) -> duplicate
        ("files", ("anita-copy.docx", _docx("Anita Rao CV", "anita@example.com", "x"), ct)),
        # a namesake of a DIFFERENT identity -> kept
        ("files", ("anita2.docx", _docx("Anita Rao", "anita.other@example.com", "9811111111"), ct)),
    ]
    r = client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"), files=files)
    assert r.status_code == 200, r.text
    sentence = r.json()["sentence"]
    assert "2 added" in sentence
    assert "1 duplicate" in sentence
    assert client.get(f"/api/roles/{req_id}/batch", headers=_as("TASpecialist")).json()["count"] == 2
