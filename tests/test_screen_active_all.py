"""The dashboard's live-progress feed: GET /api/screen/active returns every
in-flight screening job across all requisitions (one poll for the whole
Requisitions list), so a recruiter juggling several reqs can watch "Screening
N/M" tick up from the list without opening each workspace.

Offline: the memory job store is the default backend; seed it directly rather
than spawning the real `screener run` subprocess.
"""

from __future__ import annotations

import pytest

from resume_screener.api import jobs
from tests.test_sso_identity import _as


@pytest.fixture()
def store(monkeypatch):
    """A fresh in-memory job store for each test (the module singleton persists)."""
    fresh = jobs._MemoryStore()
    monkeypatch.setattr(jobs, "_MEMORY", fresh)
    return fresh


def _seed(store, job_id, req_id, **state):
    store.create(job_id, req_id)
    store.save(job_id, req_id, {"status": "running", "done": 0, "total": 0, **state})


def test_active_jobs_lists_only_running(store):
    _seed(store, "j1", "role-a", status="running", done=3, total=10)
    _seed(store, "j2", "role-b", status="done", done=5, total=5)
    _seed(store, "j3", "role-c", status="error", error="boom")

    active = jobs.active_jobs()
    assert {j["req_id"] for j in active} == {"role-a"}
    [a] = active
    assert (a["done"], a["total"]) == (3, 10)


def test_active_jobs_empty_when_nothing_running(store):
    assert jobs.active_jobs() == []


def test_screen_active_route_returns_running_jobs(store):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    _seed(store, "j1", "role-a", status="running", done=2, total=8)

    client = TestClient(app)
    resp = client.get("/api/screen/active", headers=_as("TASpecialist"))
    assert resp.status_code == 200, resp.text
    out = resp.json()["jobs"]
    assert [j["req_id"] for j in out] == ["role-a"]
    assert out[0]["done"] == 2 and out[0]["total"] == 8


def test_screen_active_route_requires_recruiter(store):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    client = TestClient(app)
    # An identity with no capabilities can't read the feed.
    resp = client.get("/api/screen/active", headers=_as("HiringManager"))
    assert resp.status_code == 403
