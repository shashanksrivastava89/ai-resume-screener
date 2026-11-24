"""Per-person rate limits (2026-10-14). Offline.

A token bucket per (action, person): a burst, then a steady refill. Over budget the
answer is 429 with a Retry-After and a plain sentence. The limiter runs after a
route's capability check, so someone without access gets their 403 and never spends
a token.
"""

from __future__ import annotations

import pytest

from resume_screener.api import ratelimit
from tests.test_security_headers import client  # noqa: F401
from tests.test_sso_identity import _as


@pytest.fixture(autouse=True)
def _limits_on(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT", "on")
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_a_bucket_allows_a_burst_then_refills():
    bucket = ratelimit.TokenBucket(capacity=2, refill_per_second=1 / 60, now=0.0)
    assert bucket.take(0.0) == 0 and bucket.take(0.0) == 0
    assert bucket.take(0.0) == pytest.approx(60.0)
    assert bucket.take(30.0) == pytest.approx(30.0)
    assert bucket.take(60.0) == 0


def test_a_budget_comes_from_the_environment_when_it_parses(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_JD_PARSE", "2/10")
    assert ratelimit.budget("jd_parse") == (2, 10.0)
    monkeypatch.setenv("RATE_LIMIT_JD_PARSE", "lots")
    assert ratelimit.budget("jd_parse") == ratelimit.DEFAULTS["jd_parse"]


def test_an_upload_budget_fits_a_thousand_resumes():
    """The client sends at most 20 files per upload request; a 1,000-résumé drop is 50
    requests and must not be cut off halfway."""
    count, minutes = ratelimit.DEFAULTS["upload"]
    assert count >= 1000 // 20 and minutes <= 10


def test_over_budget_is_a_429_with_a_retry_after(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RATE_LIMIT_JD_PARSE", "2/10")
    files = {"files": ("notes.txt", b"not a JD", "text/plain")}
    codes = [client.post("/api/jd/parse", files=files, headers=_as("TASpecialist")).status_code
             for _ in range(3)]
    assert codes == [400, 400, 429]
    r = client.post("/api/jd/parse", files=files, headers=_as("TASpecialist"))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert "try again" in r.json()["detail"]
    # someone else's bucket is their own
    other = client.post("/api/jd/parse", files=files, headers=_as("HRHead"))
    assert other.status_code == 400


def test_someone_without_access_gets_403_and_spends_nothing(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RATE_LIMIT_JD_PARSE", "1/10")
    files = {"files": ("notes.txt", b"x", "text/plain")}
    codes = {client.post("/api/jd/parse", files=files, headers=_as("IT")).status_code
             for _ in range(3)}
    assert codes == {403}


def test_the_health_probe_is_never_limited(client):  # noqa: F811
    assert {client.get("/api/health").status_code for _ in range(50)} == {200}


def test_off_switches_every_limit_off(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RATE_LIMIT", "off")
    monkeypatch.setenv("RATE_LIMIT_JD_PARSE", "1/10")
    files = {"files": ("notes.txt", b"x", "text/plain")}
    codes = {client.post("/api/jd/parse", files=files, headers=_as("TASpecialist")).status_code
             for _ in range(3)}
    assert codes == {400}


def test_every_costly_route_carries_its_limit():
    """The routes that spend model money or move data in bulk — each with the limiter
    as its LAST dependency, so the capability guard answers first."""
    from resume_screener.api.app import app

    expected = {
        ("POST", "/api/jd/parse"), ("POST", "/api/roles/{req_id}/rubric/draft"),
        ("POST", "/api/roles/{req_id}/batch/upload"), ("GET", "/api/roles/{req_id}/batch/download"),
        ("GET", "/api/roles/{req_id}/shortlist/export.csv"),
        ("POST", "/api/roles/{req_id}/shortlist/download"), ("POST", "/api/roles/{req_id}/screen"),
        ("POST", "/api/roles/{req_id}/handoff"), ("GET", "/api/audit/reports/{key}.csv"),
        ("POST", "/api/preferences/check"), ("POST", "/api/preferences"),
        ("POST", "/api/roles/{req_id}/batch/import"), ("GET", "/api/people"),
        ("POST", "/api/roles/{req_id}/checklist/check"), ("PUT", "/api/roles/{req_id}/rubric"),
    }
    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)  # an included router
            if inner is not None:
                yield from walk(inner.routes)
            else:
                yield route

    found = set()
    for route in walk(app.routes):
        deps = getattr(getattr(route, "dependant", None), "dependencies", None)
        if not deps:
            continue
        names = [getattr(d.call, "__qualname__", "") for d in deps]
        if any("limited" in n for n in names):
            assert "limited" in names[-1], route.path  # last: after the access check
            found |= {(m, route.path) for m in route.methods}
    assert expected <= found, expected - found
