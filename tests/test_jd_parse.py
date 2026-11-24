"""JD upload (permanent intake feature): file -> {title, jd_text, job_location}.

Extraction is local (pypdf / python-docx); structuring is one LLM call that
degrades to a filename/label heuristic. Tests force the heuristic path by
monkeypatching Azure config so nothing hits the network — covering both JD shapes
seen in the samples: the structured 'Job Description Template' (Position Title: /
Country:) and a free-form JD (title from the filename).
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from resume_screener.api import service
from tests.test_resume_split import make_pdf
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


def _docx(lines: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


STRUCTURED = [
    "Job Description Template",
    "Position Title: Accountant / Sr. Accountant, RTR - GL Accounting",
    "Group: G&A", "Division: Finance", "Country: Gurgaon, India",
    "Position Summary:", "Ensures GL transactions are captured accurately.",
    "Job Responsibilities", "Maintains ledger accounts and reconciliations.",
]
FREEFORM = [
    "This role is responsible for RA & QA activities in India.",
    "How You'll Make an Impact:", "Register products with CDSCO.",
]


# --- extraction -------------------------------------------------------------------


def test_extract_jd_text_docx():
    text = service.extract_jd_text("Accountant.docx", _docx(STRUCTURED))
    assert "Position Title:" in text and "Gurgaon, India" in text


def test_extract_jd_text_pdf():
    data = make_pdf([["Position Title: Supervisor, RTR", "Country: Gurgaon, India"]])
    text = service.extract_jd_text("Supervisor.pdf", data)
    assert "Supervisor" in text


def test_extract_rejects_unsupported_type():
    with pytest.raises(service.JdParseError):
        service.extract_jd_text("notes.txt", b"hello")


# --- structuring (heuristic path, no network) -------------------------------------


def test_heuristic_reads_structured_template():
    title, body, country = service._heuristic_jd("\n".join(STRUCTURED), "Accountant_Sr Accountant")
    assert title == "Accountant / Sr. Accountant, RTR - GL Accounting"
    assert country == "Gurgaon, India"
    assert "Maintains ledger accounts" in body


def test_heuristic_freeform_uses_filename():
    title, _body, country = service._heuristic_jd("\n".join(FREEFORM), "RA Specialist II - JD")
    assert title == "RA Specialist II"  # '- JD' suffix stripped
    assert country is None


def test_structure_jd_maps_country_to_jurisdiction(monkeypatch):
    monkeypatch.setattr("resume_screener.config.load_settings",
                        lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    out = service.structure_jd("\n".join(STRUCTURED), "Accountant.docx",
                               known_locations={"IN", "EU", "US-default"})
    assert out["job_location"] == "IN"
    assert out["title"].startswith("Accountant")
    assert "Maintains ledger accounts" in out["jd_text"]


# --- the route --------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app
    return TestClient(app)


def test_parse_jd_route(client, monkeypatch):
    monkeypatch.setattr("resume_screener.config.load_settings",
                        lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    r = client.post("/api/jd/parse", headers=_as("TASpecialist"),
                    files=[("files", ("Accountant.docx", _docx(STRUCTURED),
                                      "application/octet-stream"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"].startswith("Accountant")
    assert body["job_location"] == "IN"
    assert "Maintains ledger accounts" in body["jd_text"]


def test_parse_jd_route_rejects_bad_type(client):
    r = client.post("/api/jd/parse", headers=_as("TASpecialist"),
                    files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 400
