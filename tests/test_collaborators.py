"""Sharing one requisition with a colleague.

Own-req scoping (2026-07-19) is the right default and the wrong absolute: cover,
handovers and a second pair of eyes are ordinary recruiting, and the alternative was
asking an HR Head to widen someone's whole role. A collaborator is named ON THE
REQUISITION, so the grant is exactly as narrow as the need — one requisition,
revocable by the owner, and on the record.

What these pin is the boundary: it lifts own-req scoping and NOTHING else.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER = "bhavika@example.com"
MATE = "arjun2@example.com"
STRANGER = "nobody@example.com"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    for name in ("config.yaml", "rbac.yaml"):
        shutil.copyfile(REPO_ROOT / name, tmp_path / name)
    (tmp_path / "policies").mkdir()
    for pack in (REPO_ROOT / "policies").glob("*.yaml"):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    for y in (REPO_ROOT / "signals").glob("*.yaml"):
        shutil.copyfile(y, tmp_path / "signals" / y.name)
    monkeypatch.chdir(tmp_path)

    jd = tmp_path / "jds" / "analyst.md"
    jd.write_text("# Financial Analyst\n\nDo the numbers.\n", encoding="utf-8")
    (tmp_path / "jds" / "analyst.req.json").write_text(json.dumps({
        "schema": 1, "icims_req_id": "2026-1", "job_location": "IN",
        "status": "active", "created_by": OWNER,
    }), encoding="utf-8")

    from resume_screener.api.app import app
    return TestClient(app)


def _as(email: str, role: str = "TASpecialist") -> dict[str, str]:
    return {"X-Dev-User": email, "X-Dev-Role": role}


def test_a_colleague_cannot_see_it_until_they_are_invited(client):
    assert client.get("/api/roles/analyst", headers=_as(MATE)).status_code == 403


def test_the_owner_shares_it_and_they_can_open_it(client):
    r = client.post("/api/roles/analyst/collaborators",
                    json={"email": MATE, "name": "Arjun Two"}, headers=_as(OWNER))
    assert r.status_code == 200, r.text[:200]
    assert [c["email"] for c in r.json()["collaborators"]] == [MATE]
    assert client.get("/api/roles/analyst", headers=_as(MATE)).status_code == 200


def test_it_appears_on_their_dashboard_too(client):
    """Being able to open a requisition you cannot find is not access."""
    before = client.get("/api/roles", headers=_as(MATE)).json()["roles"]
    assert not any(c["req_id"] == "analyst" for c in before)
    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    after = client.get("/api/roles", headers=_as(MATE)).json()["roles"]
    assert any(c["req_id"] == "analyst" for c in after)


def test_a_collaborator_cannot_pass_the_invitation_on(client):
    """A narrow, revocable grant must not become an unbounded one behind the
    owner's back."""
    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    r = client.post("/api/roles/analyst/collaborators", json={"email": STRANGER},
                    headers=_as(MATE))
    assert r.status_code == 403
    assert client.get("/api/roles/analyst", headers=_as(STRANGER)).status_code == 403


def test_a_stranger_cannot_share_a_requisition_they_cannot_see(client):
    r = client.post("/api/roles/analyst/collaborators", json={"email": STRANGER},
                    headers=_as(STRANGER))
    assert r.status_code == 403


def test_the_owner_can_take_someone_off_again(client):
    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    r = client.delete(f"/api/roles/analyst/collaborators/{MATE}", headers=_as(OWNER))
    assert r.status_code == 200 and r.json()["collaborators"] == []
    assert client.get("/api/roles/analyst", headers=_as(MATE)).status_code == 403


def test_a_collaborator_can_step_away_without_asking(client):
    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    r = client.delete(f"/api/roles/analyst/collaborators/{MATE}", headers=_as(MATE))
    assert r.status_code == 200
    assert client.get("/api/roles/analyst", headers=_as(MATE)).status_code == 403


def test_sharing_twice_is_not_an_error(client):
    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    r = client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                    headers=_as(OWNER))
    assert r.status_code == 200
    assert len(r.json()["collaborators"]) == 1


def test_an_address_that_is_not_one_is_refused(client):
    r = client.post("/api/roles/analyst/collaborators", json={"email": "arjun"},
                    headers=_as(OWNER))
    assert r.status_code == 400


def test_sharing_grants_no_capability_the_person_lacks(client):
    """The invitation lifts own-req scoping. It is not a role."""
    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    # A hiring manager holds no `recruiter` capability, so the recruiter-only
    # surfaces stay shut whatever the requisition says.
    r = client.get("/api/roles/analyst/screen/preflight",
                   headers=_as(MATE, role="HiringManager"))
    assert r.status_code == 403


def test_the_share_is_on_the_record(client):
    from resume_screener.events import load_events

    client.post("/api/roles/analyst/collaborators", json={"email": MATE},
                headers=_as(OWNER))
    kinds = [e["kind"] for e in load_events(req_id="analyst")]
    assert "req_shared" in kinds
    assert any(e["data"].get("to") == MATE
               for e in load_events(kinds=["notify"]))
