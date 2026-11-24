"""Roles see what their pages need, and nothing more (2026-10-16, pen-test bundle).

RS-05: Legal and the approvers read the scoring copy, the flags and the locations
their pages print (they got 403s and the pages fell back to hard-coded numbers).
RS-08: without flags_view a flag is its switch — no name, description or source —
and the region catalog is administrators' reading. RS-09: one per-person budget on
every /api/ request. And three found while answering it: a reaction must be on a
comment of the same requisition and candidate, a run's status is the requisition's,
and a hiring manager's writes go through the region lens.
"""

from __future__ import annotations

import pytest

from resume_screener import comments
from resume_screener.api import jobs, ratelimit
from tests.test_hm_share import _mk_screened_role, client, workspace  # noqa: F401
from tests.test_sso_identity import _as


@pytest.mark.parametrize("path", ["/api/scoring-config", "/api/features", "/api/locations"])
def test_legal_reads_what_its_pages_print(client, path):
    assert client.get(path, headers=_as("Legal")).status_code == 200, path


def test_a_flag_without_flags_view_is_only_its_switch(client):
    ta = client.get("/api/features", headers=_as("TASpecialist")).json()["features"]
    assert ta and all(set(f) <= {"key", "enabled", "default", "regions", "scope", "control"}
                      for f in ta)
    admin = client.get("/api/features", headers=_as("SuperAdmin")).json()["features"]
    assert all("description" in f and "name" in f for f in admin)


def test_the_region_catalog_is_for_administrators(client):
    assert client.get("/api/regions", headers=_as("TASpecialist")).status_code == 403
    assert client.get("/api/regions", headers=_as("SuperAdmin")).status_code == 200


def test_every_api_request_shares_one_generous_budget(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT", "on")
    monkeypatch.setenv("RATE_LIMIT_API", "5/1")
    ratelimit.reset()
    try:
        codes = [client.get("/api/me", headers=_as("TASpecialist")).status_code for _ in range(6)]
        assert codes[:5] == [200] * 5 and codes[5] == 429
        over = client.get("/api/me", headers=_as("TASpecialist"))
        assert over.headers["Retry-After"] and over.json()["detail"] == ratelimit.MESSAGE
        assert client.get("/api/me", headers=_as("HRHead")).status_code == 200  # per person
        assert all(client.get("/api/health").status_code == 200 for _ in range(10))
    finally:
        ratelimit.reset()


def test_a_reaction_must_be_on_a_comment_of_this_candidate(client):
    stem, ids = _mk_screened_role()
    other, other_ids = _mk_screened_role(stem="treasury-analyst")
    theirs = comments.add_comment(other, other_ids[0], author="taspecialist@example.com",
                                  text="on another requisition")
    r = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments/{theirs['comment_id']}/react",
                    headers=_as("TASpecialist"), json={"emoji": "👍"})
    assert r.status_code == 404
    assert comments.reactions_for(theirs["comment_id"]) == {}
    mine = comments.add_comment(stem, ids[0], author="taspecialist@example.com", text="here")
    ok = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments/{mine['comment_id']}/react",
                     headers=_as("TASpecialist"), json={"emoji": "👍"})
    assert ok.status_code == 200


def test_a_runs_status_is_for_those_who_may_see_its_requisition(client, monkeypatch):
    from resume_screener.ats.local_folder import save_req_meta
    from pathlib import Path

    monkeypatch.setattr(jobs, "_MEMORY", jobs._MemoryStore())
    jobs._reset_live_state()
    stem, _ids = _mk_screened_role()
    save_req_meta(Path("jds") / f"{stem}.md", created_by="hrspecialist@example.com")
    jobs._MEMORY.create("j-other", stem)
    jobs._MEMORY.save("j-other", stem, {"status": "running", "done": 1, "total": 3,
                                        "message": "…", "error": ""})
    try:
        assert client.get("/api/screen/jobs/j-other", headers=_as("TASpecialist")).status_code == 403
        assert client.get("/api/screen/jobs/j-other", headers=_as("HRSpecialist")).status_code == 200
    finally:
        jobs._reset_live_state()


def test_a_hiring_managers_writes_go_through_the_region_lens(client, monkeypatch):
    from resume_screener.api import service

    stem, _ids = _mk_screened_role()
    monkeypatch.setattr(service, "region_allows", lambda req, user: False)
    r = client.post(f"/api/roles/{stem}/takes", headers=_as("HiringManager"), json={})
    assert r.status_code == 403 and "region" in r.json()["detail"]


def test_the_shortlist_csv_filename_is_sanitised():
    from resume_screener.api.app import _content_disposition, _safe_component

    name = f"shortlist-{_safe_component('REQ\"1\\r\\nSet-Cookie: x')}.csv"
    header = _content_disposition("attachment", name)
    assert "\r" not in header and "\n" not in header and header.count('"') <= 2
