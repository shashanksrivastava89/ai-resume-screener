"""B4: the New-requisition form can put a hiring manager ON RECORD at creation.

Contract: hm.status null = named for reference (no invite, no notify); the
portal's assign_hm later upgrades to status "invited" and owns notification.
The awaiting_hm metric counts only invited HMs. Offline: tmp_path + _as().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_screener.ats.local_folder import load_req_meta
from resume_screener.events import load_events
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
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _create(client, **extra):
    return client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Finance Analyst", "jd_text": "Owns treasury accounting.",
        "job_location": "IN", "icims_req_id": "IN-901", **extra,
    })


def test_hm_name_lands_in_sidecar_response_and_card(client):
    response = _create(client, hm_name="Bhavika Lillis", hm_email="bhavika_lillis.test@example.com")
    assert response.status_code == 200, response.text
    assert response.json()["hm"] == {"email": "bhavika_lillis.test@example.com",
                                     "name": "Bhavika Lillis", "status": None}

    meta = load_req_meta(Path("jds") / "finance-analyst.md")
    assert meta["hm"] == {"email": "bhavika_lillis.test@example.com",
                          "name": "Bhavika Lillis", "status": None}

    payload = client.get("/api/roles", headers=_as("TASpecialist")).json()
    [card] = payload["roles"]
    assert card["hm"]["name"] == "Bhavika Lillis"
    # named-for-reference ≠ awaiting their input
    assert payload["metrics"]["awaiting_hm"] == 0

    # on record = an activity-rail event, but NO invite/notification
    [event] = load_events(req_id="finance-analyst", kinds=["hm_assigned"])
    assert "on record" in event["summary"] and "Bhavika Lillis" in event["summary"]


def test_hm_email_validated_and_required_with_a_name(client):
    bad = _create(client, hm_name="Bhavika Lillis", hm_email="not-an-email")
    assert bad.status_code == 400
    assert "doesn't look right" in bad.json()["detail"]
    # 2026-07-19: the HM portal reaches a manager through their email, so a
    # named HM with no email is unreachable — rejected with a plain message.
    nameless = _create(client, hm_name="Bhavika Lillis")
    assert nameless.status_code == 400
    assert "email" in nameless.json()["detail"]
    ok = _create(client, hm_name="Bhavika Lillis", hm_email="bhavika_lillis@example.com")
    assert ok.status_code == 200
    assert ok.json()["hm"]["email"] == "bhavika_lillis@example.com"
    assert ok.json()["hm"]["status"] is None


def test_no_hm_fields_keeps_hm_null(client):
    response = _create(client)
    assert response.status_code == 200
    assert response.json()["hm"] is None
    assert load_req_meta(Path("jds") / "finance-analyst.md")["hm"] is None
    assert load_events(req_id="finance-analyst", kinds=["hm_assigned"]) == []


def test_assign_hm_upgrades_a_named_hm_to_invited(client, monkeypatch):
    """The portal flow stays the one place 'invited' comes from — assigning
    overwrites the on-record entry cleanly."""
    _create(client, hm_name="Bhavika Lillis", hm_email="bhavika_lillis@example.com")
    # dark-launch: the assign flow needs the hm_portal flag (env-controlled)
    monkeypatch.setenv("FEATURE_HM_PORTAL", "1")

    response = client.put("/api/roles/finance-analyst/hm", headers=_as("TASpecialist"),
                          json={"email": "bhavika_lillis@example.com", "name": "Bhavika Lillis"})
    assert response.status_code == 200, response.text
    meta = load_req_meta(Path("jds") / "finance-analyst.md")
    assert meta["hm"]["status"] == "invited"
    assert meta["hm"]["email"] == "bhavika_lillis@example.com"
    payload = client.get("/api/roles", headers=_as("TASpecialist")).json()
    assert payload["metrics"]["awaiting_hm"] == 1
