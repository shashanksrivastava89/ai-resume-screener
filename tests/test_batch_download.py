"""Résumé downloads on the Candidates tab: one file straight from the batch
(pre-screening — before the candidate_id/private-map bridge exists) and the
whole batch as a ZIP. Path traversal is refused. Offline: tmp_path workspace +
TestClient/_as()."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    import shutil

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


def _upload(client, req_id, name, data: bytes):
    r = client.post(f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"),
                    files={"files": (name, data, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_single_batch_file_download(client):
    req_id = _mk_role(client)
    _upload(client, req_id, "Anita Rao.pdf", b"%PDF-1.4 anita resume bytes")
    r = client.get(f"/api/roles/{req_id}/batch/file/Anita Rao.pdf", headers=_as("TASpecialist"))
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 anita resume bytes"
    assert "attachment" in r.headers.get("content-disposition", "")


def test_single_download_missing_file_404(client):
    req_id = _mk_role(client)
    _upload(client, req_id, "Anita Rao.pdf", b"%PDF-1.4 x")
    r = client.get(f"/api/roles/{req_id}/batch/file/Nope.pdf", headers=_as("TASpecialist"))
    assert r.status_code == 404


def test_path_traversal_is_refused(client):
    req_id = _mk_role(client)
    _upload(client, req_id, "Anita Rao.pdf", b"%PDF-1.4 x")
    # a traversal attempt is basename-reduced -> 'config.yaml' isn't in the batch
    r = client.get(f"/api/roles/{req_id}/batch/file/..%2f..%2fconfig.yaml",
                   headers=_as("TASpecialist"))
    assert r.status_code == 404


def test_batch_zip_download(client):
    req_id = _mk_role(client)
    _upload(client, req_id, "Anita Rao.pdf", b"%PDF-1.4 anita")
    _upload(client, req_id, "Vikram Shah.pdf", b"%PDF-1.4 vikram")
    r = client.get(f"/api/roles/{req_id}/batch/download", headers=_as("TASpecialist"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "resumes.zip" in r.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = sorted(zf.namelist())
        assert names == ["Anita Rao.pdf", "Vikram Shah.pdf"]
        assert zf.read("Anita Rao.pdf") == b"%PDF-1.4 anita"


def test_batch_zip_empty_is_404(client):
    req_id = _mk_role(client)
    r = client.get(f"/api/roles/{req_id}/batch/download", headers=_as("TASpecialist"))
    assert r.status_code == 404
