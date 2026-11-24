"""Manual locks (2026-07-20): a recruiter can freeze a requisition's checklist
or candidate list; only the locker, an HR Head or a Super Admin can unlock."""

from __future__ import annotations

from tests.test_role_details_api import _mk_role, client, workspace  # noqa: F401
from tests.test_sso_identity import _as

TA_EMAIL = "taspecialist@example.com"


def test_lock_surfaces_on_the_role_and_blocks_publish(client):
    stem = _mk_role(client)  # owned by TASpecialist
    r = client.post(f"/api/roles/{stem}/lock", headers=_as("TASpecialist"),
                    json={"target": "checklist"})
    assert r.status_code == 200
    assert r.json()["locks"]["checklist"]["locked_by"] == TA_EMAIL

    role = client.get(f"/api/roles/{stem}", headers=_as("TASpecialist")).json()
    assert role["locks"]["checklist"]["locked_by"] == TA_EMAIL
    assert role["locks"]["candidates"] is None

    # publishing the checklist is refused while locked
    pub = client.put(f"/api/roles/{stem}/rubric", headers=_as("TASpecialist"),
                     json={"requirements": [{"text": "GL accounting", "kind": "must_have",
                                             "importance": 3}]})
    assert pub.status_code == 409 and pub.json()["detail"]["code"] == "manually_locked"


def test_candidate_lock_blocks_batch_changes(client):
    stem = _mk_role(client)
    client.post(f"/api/roles/{stem}/lock", headers=_as("TASpecialist"),
                json={"target": "candidates"})
    r = client.post(f"/api/roles/{stem}/batch/import", headers=_as("TASpecialist"),
                    json={"folder": "some/path"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "manually_locked"


def test_only_locker_or_admin_can_unlock(client):
    stem = _mk_role(client)  # owned by TASpecialist
    # HR Head locks it (they can reach every requisition)
    client.post(f"/api/roles/{stem}/lock", headers=_as("HRHead"),
                json={"target": "candidates"})
    # the req owner is neither the locker nor an admin → cannot unlock
    denied = client.post(f"/api/roles/{stem}/unlock", headers=_as("TASpecialist"),
                         json={"target": "candidates"})
    assert denied.status_code == 403
    # the locker (also reqs_all) can
    ok = client.post(f"/api/roles/{stem}/unlock", headers=_as("HRHead"),
                     json={"target": "candidates"})
    assert ok.status_code == 200 and ok.json()["locks"]["candidates"] is None
    # unlocked → batch changes flow again (folder error, not a lock 409)
    r = client.post(f"/api/roles/{stem}/batch/import", headers=_as("TASpecialist"),
                    json={"folder": "some/path"})
    assert r.status_code == 400
