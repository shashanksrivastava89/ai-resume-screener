"""Checklist version history (2026-09-07): every published version, and — the
question that decides whether a version is safe to walk away from — whether any
candidate was ever SCREENED against it.

The answer comes from data already kept: each `screen_completed` event stamps the
rubric it ran ("stem.rubric.v2.yaml (v2)"). Restoring is deliberately a READ plus an
ordinary publish, so history stays append-only.

Offline: tmp_path workspace + synthetic events (never real output/).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.events import emit_event
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return TestClient(app)


def _role(client) -> str:
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9100"})
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def _publish(client, req_id: str, rows: list[dict]) -> int:
    r = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                   json={"requirements": rows})
    assert r.status_code == 200, r.text
    return r.json()["version"]


V1 = [{"text": "US GAAP depth", "kind": "must_have", "importance": 4},
      {"text": "CA qualification", "kind": "hard_gate"}]
V2 = [{"text": "US GAAP depth", "kind": "must_have", "importance": 5},
      {"text": "CA qualification", "kind": "hard_gate"},
      {"text": "Oracle EBS exposure", "kind": "nice_to_have", "importance": 2}]


def test_versions_list_newest_first_with_shape_and_current_flag(client):
    req_id = _role(client)
    _publish(client, req_id, V1)
    _publish(client, req_id, V2)

    versions = client.get(f"/api/roles/{req_id}/rubric/versions",
                          headers=_as("TASpecialist")).json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]  # newest first
    latest, first = versions
    assert latest["is_current"] is True and first["is_current"] is False
    assert (latest["requirement_count"], latest["gate_count"], latest["must_count"]) == (3, 1, 1)
    assert (first["requirement_count"], first["gate_count"], first["must_count"]) == (2, 1, 1)
    # who published it, from the app spine — never a bare timestamp
    assert latest["published_by"] == "taspecialist@example.com"
    assert latest["published_ts"]
    # nothing screened yet: both versions are drafts that were superseded
    assert [v["screening_runs"] for v in versions] == [0, 0]
    assert all(v["last_screened_ts"] is None for v in versions)


def test_a_version_that_was_screened_against_says_so(client):
    req_id = _role(client)
    _publish(client, req_id, V1)
    # a run against v1 — the same stamp shape jobs.py emits
    emit_event("screen_completed", actor="taspecialist@example.com", req_id=req_id,
               summary="Screened 4 candidates",
               data={"run_id": "run-1", "rubric": f"{req_id}.rubric.v1.yaml (v1)"})
    _publish(client, req_id, V2)

    versions = {v["version"]: v for v in
                client.get(f"/api/roles/{req_id}/rubric/versions",
                           headers=_as("TASpecialist")).json()["versions"]}
    assert versions[1]["screening_runs"] == 1
    assert versions[1]["last_screened_ts"]  # when the run happened
    assert versions[2]["screening_runs"] == 0  # published after the run


def test_run_meta_counts_as_a_run_for_a_pre_spine_screen(client):
    """A run from before the event spine only survives in run_meta — it still means
    the version was used, so the history must not call that version unused."""
    req_id = _role(client)
    _publish(client, req_id, V1)
    out = Path("output") / req_id
    out.mkdir(parents=True)
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": "old-run", "started_utc": "2026-08-01T07:00:00+00:00",
        "jurisdiction": "IN", "policy_pack": "IN", "counts": {"scored": 2},
        "versions": {"rubric": f"{req_id}.rubric.v1.yaml (v1)"},
    }), encoding="utf-8")

    versions = client.get(f"/api/roles/{req_id}/rubric/versions",
                          headers=_as("TASpecialist")).json()["versions"]
    assert versions[0]["version"] == 1 and versions[0]["screening_runs"] == 1


def test_one_version_reads_back_in_the_editor_row_shape(client):
    req_id = _role(client)
    _publish(client, req_id, V1)
    _publish(client, req_id, V2)

    got = client.get(f"/api/roles/{req_id}/rubric/versions/1",
                     headers=_as("TASpecialist")).json()
    assert got["version"] == 1
    rows = got["requirements"]
    assert [r["text"] for r in rows] == ["US GAAP depth", "CA qualification"]
    assert rows[1]["kind"] == "hard_gate"  # gates carry no importance
    # An old version reads back through the editor's OWN row converter, so it lands
    # in the editor exactly the way the current version does — same keys, same
    # importance mapping, ready to publish as the next version.
    current = client.get(f"/api/roles/{req_id}/rubric",
                         headers=_as("TASpecialist")).json()["requirements"]
    assert set(rows[0]) == set(current[0])
    assert rows[0]["kind"] == "must_have" and 1 <= rows[0]["importance"] <= 5
    # restoring is a READ + the ordinary publish: nothing here rewrote v1 or v2
    after = client.get(f"/api/roles/{req_id}/rubric/versions",
                       headers=_as("TASpecialist")).json()["versions"]
    assert [v["version"] for v in after] == [2, 1]


def test_unknown_version_is_404(client):
    req_id = _role(client)
    _publish(client, req_id, V1)
    assert client.get(f"/api/roles/{req_id}/rubric/versions/9",
                      headers=_as("TASpecialist")).status_code == 404
