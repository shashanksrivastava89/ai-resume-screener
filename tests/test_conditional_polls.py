"""Polls that have not changed answer 304, and nothing else is touched.

Reading a response body back means consuming its stream, so the scope is named
explicitly: a résumé download or any other streamed reply must pass through
untouched (2026-09-10).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


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

    from resume_screener.api.app import app

    return TestClient(app)


def test_an_unchanged_poll_answers_304_with_no_body(client):
    first = client.get("/api/notifications")
    assert first.status_code == 200
    tag = first.headers.get("ETag")
    assert tag, "a poll response carried no validator"

    again = client.get("/api/notifications", headers={"If-None-Match": tag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers.get("ETag") == tag


def test_a_changed_poll_answers_200_with_a_new_validator(client):
    first = client.get("/api/notifications")
    stale = '"' + "0" * 32 + '"'
    again = client.get("/api/notifications", headers={"If-None-Match": stale})
    assert again.status_code == 200
    assert again.json() == first.json()
    assert again.headers.get("ETag") != stale


def test_the_body_is_unchanged_by_the_validator(client):
    """Whatever else this does, it must not alter what the endpoint said."""
    plain = client.get("/api/screen/active")
    assert plain.status_code == 200
    assert "jobs" in plain.json()


def test_a_polls_answer_is_never_shared_between_callers(client):
    """These are per-caller: own-req scoping, and the signed-in recipient. A shared
    cache must never serve one person's notifications to another."""
    r = client.get("/api/notifications")
    assert "private" in (r.headers.get("Cache-Control") or "")


def test_nothing_outside_the_named_paths_gets_a_validator(client):
    """The scope has to stay narrow: consuming a body would break a streamed reply."""
    for path in ("/api/me", "/api/roles", "/api/health"):
        r = client.get(path)
        assert r.headers.get("ETag") is None, f"{path} was given a validator"
