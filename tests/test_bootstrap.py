"""One request for the whole app shell.

Opening any page fired seven requests in two dependent waves — identity first, then a
burst of five, then one that had waited for the burst. Each wave is a full round trip
before anything renders, and on the single hosted worker the later ones queue behind
the earlier ones (2026-09-10).

/api/bootstrap is a delivery envelope, NOT a second implementation: every part is
produced by the same handler that serves it alone. What these pin is that it cannot
become a way around the capability guards on those handlers.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("REQUIRE_SSO", "1")
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

    from resume_screener.api.app import app

    return TestClient(app)


def _parts(client, role: str) -> set[str]:
    r = client.get("/api/bootstrap", headers=_as(role))
    assert r.status_code == 200, r.text[:200]
    return set(r.json())


def test_the_shell_arrives_in_one_request(client):
    body = client.get("/api/bootstrap", headers=_as("TASpecialist")).json()
    assert body["me"]["user"] == "taspecialist@example.com"
    assert "features" in body and "directory" in body and "notifications" in body


def test_each_part_matches_the_endpoint_that_serves_it(client):
    """A second implementation would drift. These must be the same bytes."""
    head = _as("TASpecialist")
    body = client.get("/api/bootstrap", headers=head).json()
    assert body["me"] == client.get("/api/me", headers=head).json()
    assert body["features"] == client.get("/api/features", headers=head).json()
    assert body["directory"] == client.get("/api/people/directory", headers=head).json()


def test_it_never_hands_over_something_the_caller_may_not_read(client):
    """Legal has no recruiter capability, so it has no live screens to watch — the
    envelope must drop that part, not carry it. (Legal does read the feature flags
    since 2026-10-16: its read-only workspace pages are flag-gated too.)"""
    legal = _as("Legal")
    assert "active_screens" not in _parts(client, "Legal")
    assert "me" in _parts(client, "Legal")
    assert client.get("/api/features", headers=legal).status_code == 200
    assert "features" in _parts(client, "Legal")


def test_a_recruiter_gets_the_recruiter_parts(client):
    parts = _parts(client, "TASpecialist")
    assert {"me", "features", "directory", "notifications", "active_screens"} <= parts


def test_an_unassigned_principal_gets_no_capabilities_and_no_parts(client):
    """Zero capabilities is the "access not configured" case; it must not leak."""
    body = client.get("/api/bootstrap",
                      headers=_as("NotARole")).json()
    assert body["me"]["capabilities"] == []
    assert "features" not in body and "active_screens" not in body
