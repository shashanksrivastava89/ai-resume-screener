"""Résumés-before-checklist must never 500 (the role_stage progress fix), one
broken role must never take the dashboard down (role_summary_safe's degraded
card), malformed candidate records are skipped, and unhandled errors surface as
parseable JSON — the B1 resilience package.

Offline: tmp_path workspaces + the TestClient/_as() pattern from
tests/test_sso_identity.py. All fixtures are synthetic — never real output/.
"""

from __future__ import annotations

import json
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


def _create_role(client, title="Finance Analyst", icims="IN-501"):
    response = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": title, "jd_text": "Owns treasury accounting and audits.",
        "job_location": "IN", "icims_req_id": icims,
    })
    assert response.status_code == 200
    return response.json()["req_id"]


def _upload(client, req_id, name="candidate-one.pdf", data=b"%PDF-1.4 synthetic"):
    response = client.post(
        f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"),
        files=[("files", (name, data, "application/pdf"))])
    assert response.status_code == 200
    return response


# --- résumés before a checklist exists: the exact state that used to 500 -------------


def test_upload_before_checklist_keeps_role_and_dashboard_alive(client):
    stem = _create_role(client)
    _upload(client, stem)

    role = client.get(f"/api/roles/{stem}", headers=_as("TASpecialist"))
    assert role.status_code == 200
    body = role.json()
    assert body["stage"] == "draft_checklist"
    assert body["progress"] == "No checklist yet · 1 candidate"
    assert body["batch_count"] == 1
    assert body["checklist_ready"] is False

    _upload(client, stem, name="candidate-two.pdf", data=b"%PDF-1.4 other")
    listing = client.get("/api/roles", headers=_as("TASpecialist"))
    assert listing.status_code == 200
    [card] = listing.json()["roles"]
    assert card["progress"] == "No checklist yet · 2 candidates"
    assert "unavailable" not in card  # a real card, not the degraded fallback


def test_no_batch_no_checklist_progress_copy(client):
    stem = _create_role(client)
    body = client.get(f"/api/roles/{stem}", headers=_as("TASpecialist")).json()
    assert body["progress"] == "No checklist yet"
    assert body["stage"] == "draft_checklist"


# --- one broken role degrades to a card; the list survives ---------------------------


def test_one_broken_role_never_takes_the_list_down(client, monkeypatch):
    from resume_screener.api import service

    _create_role(client, title="Role A", icims="IN-1")
    _create_role(client, title="Role B", icims="IN-2")

    original = service.role_summary

    def boom(cfg, req, *args, **kwargs):
        if req.req_id == "role-a":
            raise RuntimeError("synthetic on-disk corruption")
        return original(cfg, req, *args, **kwargs)

    monkeypatch.setattr(service, "role_summary", boom)

    listing = client.get("/api/roles", headers=_as("TASpecialist"))
    assert listing.status_code == 200
    payload = listing.json()
    cards = {c["req_id"]: c for c in payload["roles"]}
    assert set(cards) == {"role-a", "role-b"}  # degraded, never hidden
    broken = cards["role-a"]
    assert broken["unavailable"] is True
    assert broken["stage"] == "unavailable"
    assert broken["stage_label"] == "Needs attention"
    assert broken["req_code"] == "IN-1"
    assert cards["role-b"].get("unavailable") is None
    # metrics still compute over the full visible set
    assert payload["metrics"]["total"] == 2


def test_malformed_candidate_record_is_skipped(client):
    stem = _create_role(client)
    folder = Path("output") / stem / "candidates"
    folder.mkdir(parents=True)
    (folder / "good.json").write_text(json.dumps(
        {"score": {"candidate_id": "c1", "fit_band": "Strong", "final_score": 80.0}}),
        encoding="utf-8")
    (folder / "bad.json").write_text(json.dumps({"nope": 1}), encoding="utf-8")
    (folder / "worse.json").write_text("{not json", encoding="utf-8")

    from resume_screener.api import service

    records = service.read_candidate_records(stem)
    assert [r["score"]["candidate_id"] for r in records] == ["c1"]
    # and the role view stays a 200 despite the junk on disk
    assert client.get(f"/api/roles/{stem}", headers=_as("TASpecialist")).status_code == 200


# --- unhandled errors become parseable JSON ------------------------------------------


def test_unhandled_error_returns_plain_json_500(workspace, monkeypatch):
    from fastapi.testclient import TestClient

    from resume_screener.api import service
    from resume_screener.api.app import app

    raw = TestClient(app, raise_server_exceptions=False)
    _create_role(raw, title="Role C", icims="IN-3")

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic explosion")

    monkeypatch.setattr(service, "role_stage", boom)
    response = raw.get("/api/roles/role-c", headers=_as("TASpecialist"))
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "tell IT" in detail
    assert "RuntimeError" not in detail  # no traceback leaks to the client
