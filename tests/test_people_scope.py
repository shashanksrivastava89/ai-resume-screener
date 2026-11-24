"""The people directory is scoped to the caller's requisitions (2026-10-16, pen-test RS-03).

/api/people/directory gave every recruiter and hiring manager everyone the app had
ever seen, with job titles and photos, and /api/bootstrap gave it even to a sign-in
with no role. Now a scoped role gets the people on the requisitions it can see;
roles that see every requisition get everyone; no role gets no directory. The
search route needs two letters, returns ten at most and has a budget.
"""

from __future__ import annotations

import urllib.error

from resume_screener import events, people
from tests.test_people import client, workspace  # noqa: F401
from tests.test_sso_identity import _as

TA = "taspecialist@example.com"
HM = "hiringmanager@example.com"
HRS = "hrspecialist@example.com"
HEAD = "hrhead@example.com"
STRANGER = "stranger@example.com"


def _world(client) -> tuple[str, str]:
    for email, name in ((TA, "Tara Roach"), (HM, "Taine Ganly"), (HRS, "Hema Rao"),
                        (HEAD, "Harsh Hickey"), (STRANGER, "Sam Trent")):
        people.remember(email, name=name, source="sso")
    mine = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash.", "job_location": "IN",
        "icims_req_id": "2026-7001", "hm_name": "Taine Ganly", "hm_email": HM})
    theirs = client.post("/api/roles", headers=_as("HRSpecialist"), json={
        "title": "Payroll Lead", "jd_text": "Owns payroll.", "job_location": "IN",
        "icims_req_id": "2026-7002"})
    assert mine.status_code == 200 and theirs.status_code == 200, (mine.text, theirs.text)
    return mine.json()["req_id"], theirs.json()["req_id"]


def _emails(client, role) -> set[str]:
    r = client.get("/api/people/directory", headers=_as(role))
    assert r.status_code == 200, r.text
    return {p["email"] for p in r.json()["people"]}


def test_a_recruiter_sees_the_people_on_their_own_requisitions(client):
    mine, _theirs = _world(client)
    assert _emails(client, "TASpecialist") == {TA, HM}
    # someone who acts on the requisition joins its people
    events.emit_event("req_note", actor=HEAD, req_id=mine, summary="looked in")
    assert _emails(client, "TASpecialist") == {TA, HM, HEAD}


def test_a_hiring_manager_sees_the_people_on_the_requisition_they_hold(client):
    _world(client)
    got = _emails(client, "HiringManager")
    assert TA in got and HM in got
    assert HRS not in got and STRANGER not in got


def test_roles_that_see_every_requisition_see_everyone(client):
    _world(client)
    assert {TA, HM, HRS, HEAD, STRANGER} <= _emails(client, "HRHead")


def test_bootstrap_carries_the_same_scoped_directory_and_none_without_a_role(client):
    _world(client)
    head = _as("TASpecialist")
    body = client.get("/api/bootstrap", headers=head).json()
    assert body["directory"] == client.get("/api/people/directory", headers=head).json()
    nobody = client.get("/api/bootstrap", headers=_as("NotARole")).json()
    assert "directory" not in nobody


def test_search_needs_two_letters_and_returns_ten_at_most(client):
    for i in range(14):
        people.remember(f"anand{i:02d}@example.com", name=f"Jayne {i:02d}", source="sso")
    ta = _as("TASpecialist")
    short = client.get("/api/people?q=a", headers=ta)
    assert short.status_code == 400 and "two letters" in short.json()["detail"]
    many = client.get("/api/people?q=anand&limit=50", headers=ta).json()["people"]
    assert len(many) == 10
    assert people.search("a")["people"] == []


def test_an_unreachable_directory_says_so_without_naming_the_network(workspace, monkeypatch):
    monkeypatch.setenv("PEOPLE_DIRECTORY", "graph")

    def down(url, headers):
        raise urllib.error.URLError("proxy.corp.internal:8080 refused the connection")

    monkeypatch.setattr(people, "_graph_singleton",
                        people.GraphDirectory(token_provider=lambda: "t", fetch=down))
    result = people.search("someone")
    assert result["note"] == ("The company directory couldn't be reached — type the "
                              "address instead.")
    assert "proxy" not in people.status()["graph"]["reason"]
