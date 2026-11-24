"""A requisition's job location is stated, never assumed (global rollout, 2026-09-12).

THE BUG THIS SUITE HOLDS SHUT. `POST /api/roles` used to fall back to
`config.policy.default_jurisdiction` — India — whenever the payload carried no
job location, and `GET /api/locations` sorted India first so an untouched
dropdown produced one too. A recruiter in Budapest therefore created an India
requisition by doing nothing wrong: the India pack keeps candidate details 400
days instead of the EU's 180, and scores `language_evidence` at cap 3 where the
EU pack disables it precisely because a language requirement can proxy
nationality there. Nothing on screen said so.

So: no location, no requisition. The refusals name the country where they can,
because "pick something else" is not an answer a recruiter can act on.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_sso_identity import principal

REPO_ROOT = Path(__file__).resolve().parents[1]


def _as(role: str) -> dict:
    return {"X-MS-CLIENT-PRINCIPAL": principal(f"{role.lower()}@example.com", [role])}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(REPO_ROOT / "policies", tmp_path / "policies")
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)

    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _create(client, **overrides) -> "object":
    payload = {"title": "Quality Specialist", "jd_text": "Owns the QMS.",
               "icims_req_id": "REQ-1"}
    payload.update(overrides)
    return client.post("/api/roles", headers=_as("TASpecialist"), json=payload)


# --- no silent default -------------------------------------------------------------


def test_creating_without_a_location_is_refused_and_nothing_is_written(client):
    response = _create(client)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "country" in detail["message"].lower()
    assert detail["field"] == "job_location"
    # and no half-made requisition is left behind
    assert list(Path("jds").glob("*.md")) == []


def test_an_unknown_pack_code_is_refused(client):
    response = _create(client, job_location="ZZ")
    assert response.status_code == 400
    assert "ZZ" in response.json()["detail"]["message"]
    assert list(Path("jds").glob("*.md")) == []


# --- Hungary: the requisition that used to become India -----------------------------


def test_a_hungarian_requisition_lands_on_the_eu_pack_and_remembers_hungary(client):
    from resume_screener.ats.local_folder import load_req_meta

    response = _create(client, job_location="EU", country="HU")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["job_location"], body["country"]) == ("EU", "HU")

    meta = load_req_meta(Path("jds") / f"{body['req_id']}.md")
    assert meta["job_location"] == "EU"
    assert meta["country"] == "HU"

    # the card reads Hungary, and its clock is Budapest's — not Brussels'
    [card] = client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]
    assert card["location_display"] == "Hungary"
    assert card["tz"] == "Europe/Budapest"
    assert "GDPR" in card["law"]
    assert card["region"] == "EMEA"


def test_the_pack_and_the_country_have_to_agree(client):
    response = _create(client, job_location="IN", country="HU")
    assert response.status_code == 400
    assert response.json()["detail"]["field"] == "country"


# --- a country we know but have no rules for ---------------------------------------


def test_canada_is_refused_by_name_not_screened_under_california(client):
    response = _create(client, job_location="US-CA", country="CA")
    assert response.status_code == 400
    message = response.json()["detail"]["message"]
    assert "Canada" in message and "HR and Legal" in message
    assert list(Path("jds").glob("*.md")) == []


# --- the United States: the state decides, and one city decides for itself ----------


def test_california_and_the_federal_baseline_are_different_requisitions(client):
    ca = _create(client, job_location="US-CA", country="US", us_state="CA",
                 icims_req_id="REQ-CA")
    assert ca.status_code == 200, ca.text
    assert ca.json()["us_state"] == "CA"

    tx = _create(client, title="Analyst TX", job_location="US-default", country="US",
                 us_state="TX", icims_req_id="REQ-TX")
    assert tx.status_code == 200, tx.text

    cards = {c["req_id"]: c for c in
             client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]}
    assert cards[ca.json()["req_id"]]["tz"] == "America/Los_Angeles"
    assert cards[tx.json()["req_id"]]["tz"] == "America/Chicago"


def test_new_york_city_carries_local_law_144_and_the_state_alone_does_not(client):
    nyc = _create(client, job_location="US-NYC", country="US", us_state="NY",
                  nyc=True, icims_req_id="REQ-NYC")
    assert nyc.status_code == 200, nyc.text

    albany = _create(client, title="Analyst Albany", job_location="US-default",
                     country="US", us_state="NY", nyc=False, icims_req_id="REQ-ALB")
    assert albany.status_code == 200, albany.text

    # and claiming NYC's law for a job that isn't there is refused
    mismatch = _create(client, title="Analyst Buffalo", job_location="US-NYC",
                       country="US", us_state="NY", nyc=False, icims_req_id="REQ-BUF")
    assert mismatch.status_code == 400


# --- editing a live requisition ----------------------------------------------------


def test_moving_a_requisition_to_another_country_moves_both_fields(client):
    from resume_screener.ats.local_folder import load_req_meta

    made = _create(client, job_location="IN", country="IN")
    req_id = made.json()["req_id"]

    moved = client.put(f"/api/roles/{req_id}/details", headers=_as("TASpecialist"),
                       json={"job_location": "EU", "country": "HU"})
    assert moved.status_code == 200, moved.text

    meta = load_req_meta(Path("jds") / f"{req_id}.md")
    assert (meta["job_location"], meta["country"]) == ("EU", "HU")


def test_an_edit_cannot_leave_a_country_behind_its_pack(client):
    made = _create(client, job_location="IN", country="IN")
    req_id = made.json()["req_id"]
    bad = client.put(f"/api/roles/{req_id}/details", headers=_as("TASpecialist"),
                     json={"job_location": "EU", "country": "IN"})
    assert bad.status_code == 400
