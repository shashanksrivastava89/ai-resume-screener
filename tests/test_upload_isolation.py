"""Résumé upload must not freeze the app (2026-07-23).

The batch-upload and JD-parse handlers do blocking work (file reads, content
hashing, PDF splitting). If they run as `async def`, that work sits on the
single event loop and every OTHER requisition's requests (uploads, screening,
navigation) stall until the upload finishes. They must be plain `def` so
Starlette runs them in its threadpool, off the loop — and the bulk add must
hash the existing batch once, not per file (O(n²)). Offline: tmp_path workspace
+ TestClient/_as()."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import pytest

from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _mk_role(client, title="Finance Analyst", icims="IN-900"):
    r = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": title, "jd_text": "Owns the monthly close.", "job_location": "IN",
        "icims_req_id": icims,
    })
    assert r.status_code == 200, r.text
    return r.json()["req_id"]


def test_upload_and_jd_parse_handlers_are_sync_not_coroutines():
    """The regression guard for the freeze: a coroutine handler runs ON the event
    loop; a sync def runs in the threadpool. These must stay sync."""
    from resume_screener.api.app import parse_jd_route, upload_batch

    assert not inspect.iscoroutinefunction(upload_batch), (
        "upload_batch must be a sync def so a large upload runs in the threadpool "
        "and never blocks the event loop (which would freeze every other req).")
    assert not inspect.iscoroutinefunction(parse_jd_route), (
        "parse_jd_route must be a sync def for the same reason.")


def test_bulk_upload_in_one_request_dedupes_by_content(client):
    """Many files in one multipart POST: content-hash dedupe holds even though the
    existing batch is hashed once up front (the amortized `known` set is extended
    as each file lands)."""
    req_id = _mk_role(client)
    files = [
        ("files", ("Anita Rao.pdf", b"%PDF-1.4 anita", "application/pdf")),
        ("files", ("Vikram Shah.pdf", b"%PDF-1.4 vikram", "application/pdf")),
        # same CONTENT as Anita under a new name -> one candidate, a duplicate
        ("files", ("Anita copy.pdf", b"%PDF-1.4 anita", "application/pdf")),
        ("files", ("notes.txt", b"not a resume", "text/plain")),  # unsupported
    ]
    r = client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"), files=files)
    assert r.status_code == 200, r.text
    sentence = r.json()["sentence"]
    assert "2 added" in sentence
    assert "1 duplicate" in sentence
    assert "1 unsupported" in sentence
    # the batch holds exactly the two distinct résumés
    batch = client.get(f"/api/roles/{req_id}/batch", headers=_as("TASpecialist")).json()
    assert batch["count"] == 2


def test_second_upload_dedupes_against_the_existing_batch(client):
    """The up-front hash of the existing batch still catches a re-upload of a file
    already on disk from a prior request."""
    req_id = _mk_role(client)
    first = [("files", ("Anita Rao.pdf", b"%PDF-1.4 anita", "application/pdf"))]
    client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"), files=first)
    again = [("files", ("Anita again.pdf", b"%PDF-1.4 anita", "application/pdf"))]
    r = client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"), files=again)
    assert r.status_code == 200
    assert "1 duplicate" in r.json()["sentence"]
    assert client.get(f"/api/roles/{req_id}/batch", headers=_as("TASpecialist")).json()["count"] == 1


def test_uploads_to_two_reqs_are_independent(client):
    """Adding résumés to one requisition never touches another's batch — each req's
    upload is isolated (the batch lock is per-req; there is no global serializer)."""
    req_a = _mk_role(client, title="Finance Analyst", icims="IN-900")
    req_b = _mk_role(client, title="Treasury Analyst", icims="IN-901")
    client.post(f"/api/roles/{req_a}/batch/upload", headers=_as("TASpecialist"),
                files=[("files", ("a1.pdf", b"%PDF a1", "application/pdf")),
                       ("files", ("a2.pdf", b"%PDF a2", "application/pdf"))])
    client.post(f"/api/roles/{req_b}/batch/upload", headers=_as("TASpecialist"),
                files=[("files", ("b1.pdf", b"%PDF b1", "application/pdf"))])
    assert client.get(f"/api/roles/{req_a}/batch", headers=_as("TASpecialist")).json()["count"] == 2
    assert client.get(f"/api/roles/{req_b}/batch", headers=_as("TASpecialist")).json()["count"] == 1
