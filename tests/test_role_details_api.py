"""B3: GET /api/roles/{id}/jd (the full JD, decomposed) and PUT
/api/roles/{id}/details (edit title/JD/location/iCIMS after creation).

Pins the load-bearing invariants: the slug/req_id/filename NEVER change on a
title edit, the résumé batch survives one (batches are keyed by req_id now),
display_title tracks the JD heading, clash checks exclude self and return the
structured 409, and the change lands on the event spine with a changed-fields
list. Offline: tmp_path workspace + TestClient/_as().
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
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


JD_BODY = "Owns treasury accounting and audits.\n\nResponsibilities:\n- GST/TDS filings"


def _mk_role(client, title="Finance Analyst", icims="IN-801"):
    response = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": title, "jd_text": JD_BODY, "job_location": "IN",
        "icims_req_id": icims,
    })
    assert response.status_code == 200, response.text
    return response.json()["req_id"]


def _add_resume(client, req_id, name="anita-rao.pdf"):
    response = client.post(
        f"/api/roles/{req_id}/batch/upload", headers=_as("TASpecialist"),
        files=[("files", (name, b"%PDF-1.4 synthetic", "application/pdf"))])
    assert response.status_code == 200


# --- GET /jd --------------------------------------------------------------------------


def test_get_jd_round_trips_title_and_body(client):
    stem = _mk_role(client)
    jd = client.get(f"/api/roles/{stem}/jd", headers=_as("TASpecialist")).json()
    assert jd["title"] == "Finance Analyst"
    assert jd["jd_text"] == JD_BODY  # exactly what was typed — no '#' heading
    assert "#" not in jd["jd_text"].splitlines()[0]


def test_get_jd_404s_when_the_file_is_gone(client):
    """In the file backend the JD file IS the role's identity — deleting it 404s
    the role itself (the route's own file-missing 404 covers the DB backend,
    where a row can outlive a stale source_ref)."""
    stem = _mk_role(client)
    Path("jds", f"{stem}.md").unlink()
    response = client.get(f"/api/roles/{stem}/jd", headers=_as("TASpecialist"))
    assert response.status_code == 404


# --- PUT /details ----------------------------------------------------------------------


def test_details_records_hiring_manager_without_portal(client):
    """The HM is plain metadata — recording a name/email via /details works with
    the portal flag OFF (default) and never sets an 'invited' status."""
    stem = _mk_role(client)
    resp = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                      json={"hm_name": "Bhavika Lillis", "hm_email": "bhavika.lillis@example.com"})
    assert resp.status_code == 200, resp.text
    hm = load_req_meta(Path("jds", f"{stem}.md")).get("hm")
    assert hm["name"] == "Bhavika Lillis"
    assert hm["email"] == "bhavika.lillis@example.com"
    assert hm["status"] is None  # named on record, NOT invited
    # and it surfaces on the role detail
    detail = client.get(f"/api/roles/{stem}", headers=_as("TASpecialist")).json()
    assert detail["hm"]["name"] == "Bhavika Lillis"


def test_details_hm_name_needs_an_email(client):
    """2026-07-19: the HM portal reaches a manager through their email — a
    named HM with no email is unreachable, so /details rejects it plainly."""
    stem = _mk_role(client)
    resp = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                      json={"hm_name": "Bhavika Lillis"})
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"]
    assert load_req_meta(Path("jds", f"{stem}.md")).get("hm") is None


def test_details_rejects_a_bad_hm_email(client):
    stem = _mk_role(client)
    resp = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                      json={"hm_email": "not-an-email"})
    assert resp.status_code == 400


def test_title_edit_keeps_req_id_file_and_batch(client):
    stem = _mk_role(client)
    _add_resume(client, stem)
    response = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                          json={"title": "Senior Finance Analyst"})
    assert response.status_code == 200, response.text
    body = response.json()
    # identity is immutable; the display title tracks the new heading
    assert body["req_id"] == stem
    assert body["title"] == "Senior Finance Analyst"
    assert Path("jds", f"{stem}.md").exists()  # same file, rewritten in place
    assert Path("jds", f"{stem}.md").read_text(encoding="utf-8").startswith(
        "# Senior Finance Analyst\n")
    # the résumé batch survived the rename (keyed by req_id, not title)
    assert body["batch_count"] == 1
    # and the list card shows the new title too
    [card] = client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]
    assert card["title"] == "Senior Finance Analyst"


def test_title_edit_shows_even_after_a_rubric_exists(client):
    """display_title prefers the JD heading over the rubric's stored title —
    otherwise a rename would never show once a checklist was published."""
    stem = _mk_role(client)
    client.put(f"/api/roles/{stem}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [{"text": "US GAAP", "kind": "must_have", "importance": 4}]})
    client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
               json={"title": "Treasury Lead"})
    role = client.get(f"/api/roles/{stem}", headers=_as("TASpecialist")).json()
    assert role["title"] == "Treasury Lead"


def test_jd_edit_preserves_title_and_round_trips(client):
    stem = _mk_role(client)
    new_body = "Fresh responsibilities.\n\n- Ind AS close"
    client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
               json={"jd_text": new_body})
    jd = client.get(f"/api/roles/{stem}/jd", headers=_as("TASpecialist")).json()
    assert jd["title"] == "Finance Analyst"  # untouched
    assert jd["jd_text"] == new_body


def test_location_and_icims_updates_merge_into_the_sidecar(client):
    stem = _mk_role(client)
    response = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                          json={"job_location": "SG", "icims_req_id": "IN-802"})
    assert response.status_code == 200
    meta = load_req_meta(Path("jds") / f"{stem}.md")
    assert meta["job_location"] == "SG"
    assert meta["icims_req_id"] == "IN-802"
    assert meta["created_by"]  # untouched keys preserved (read-merge-write)
    assert response.json()["location_display"] == "Singapore"


def test_put_validation_messages(client):
    stem = _mk_role(client)
    cases = [
        ({}, "Nothing to update."),
        ({"title": "  "}, "The job title can't be empty."),
        ({"jd_text": ""}, "The job description can't be empty."),
        ({"icims_req_id": ""}, "iCIMS Req ID is required — clearing it would "
                               "break the mapping to the requisition."),
    ]
    for payload, message in cases:
        response = client.put(f"/api/roles/{stem}/details",
                              headers=_as("TASpecialist"), json=payload)
        assert response.status_code == 400, payload
        assert response.json()["detail"] == message
    bad_loc = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                         json={"job_location": "XX"})
    assert bad_loc.status_code == 400
    # structured now (the form pins it to the field), same as create
    assert "hiring rules" in bad_loc.json()["detail"]["message"]
    assert bad_loc.json()["detail"]["field"] == "job_location"


def test_icims_clash_excludes_self_and_is_structured(client):
    stem = _mk_role(client, title="Role One", icims="IN-810")
    _mk_role(client, title="Role Two", icims="IN-811")
    # keeping my own id is never a clash
    ok = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                    json={"icims_req_id": "IN-810"})
    assert ok.status_code == 200
    # taking another role's id is the structured 409
    clash = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                       json={"icims_req_id": "IN-811"})
    assert clash.status_code == 409
    detail = clash.json()["detail"]
    assert detail["field"] == "icims_req_id"
    assert detail["req_id"] == "role-two"
    assert "IN-811" in detail["message"]


def test_details_update_lands_on_the_event_spine(client):
    stem = _mk_role(client)
    client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
               json={"title": "Treasury Lead", "job_location": "SG"})
    [event] = load_events(req_id=stem, kinds=["req_details_updated"])
    assert event["actor"] == "taspecialist@example.com"
    assert set(event["data"]["changed"]) == {"title", "job location"}
    assert "title" in event["summary"]


def test_noop_put_writes_nothing_and_emits_nothing(client):
    stem = _mk_role(client)
    before = Path("jds", f"{stem}.md").read_text(encoding="utf-8")
    response = client.put(f"/api/roles/{stem}/details", headers=_as("TASpecialist"),
                          json={"title": "Finance Analyst", "jd_text": JD_BODY})
    assert response.status_code == 200
    assert Path("jds", f"{stem}.md").read_text(encoding="utf-8") == before
    assert load_events(req_id=stem, kinds=["req_details_updated"]) == []


def test_same_title_creates_a_suffixed_req(client):
    """Two requisitions may share a display title (2026-07-20) — the iCIMS Req ID
    is the unique key, not the name. The second role's immutable req_id gets a
    numeric suffix (its batch/output never collide); the JD heading keeps the
    real, duplicate title."""
    first = _mk_role(client, title="Role One", icims="IN-820")
    assert first == "role-one"
    response = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Role One", "jd_text": "body", "icims_req_id": "IN-821",
        "job_location": "IN"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["req_id"] == "role-one-2"
    assert body["title"] == "Role One"
    # both are live and distinct
    roles = client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]
    ids = {r["req_id"] for r in roles}
    assert {"role-one", "role-one-2"} <= ids


def test_freed_req_id_is_reusable_after_delete(client):
    """A req_id freed by deleting its role is reusable — delete removes every
    <stem>.* file, so the next same-title create reclaims the base slug."""
    _mk_role(client, title="Role One", icims="IN-820")
    r = client.request("DELETE", "/api/roles/role-one", headers=_as("TASpecialist"),
                       json={"confirm": True})
    assert r.status_code == 200, r.text
    again = _mk_role(client, title="Role One", icims="IN-822")
    assert again == "role-one"
