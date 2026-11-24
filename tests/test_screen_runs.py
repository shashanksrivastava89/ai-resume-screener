"""GET /api/roles/{req_id}/screen/runs — the persistent screening-run history
(who ran it, when, how it ended), composed from the app-event spine with the
same honesty rule as the audit trail: pre-spine runs fall back to the latest
run_meta as ONE entry plus a note, never fabricated history.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

STEM = "finance-analyst"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "jds" / f"{STEM}.md").write_text("# Finance Analyst\n\nbody",
                                                 encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _write_events(entries: list[dict]) -> None:
    """Events with CONTROLLED timestamps (emit_event stamps now(), which would
    make same-second ordering flaky)."""
    path = Path("signals/events.jsonl")
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def _completed(ts: str, run_id: str, actor: str, **counts) -> dict:
    merged = {"scored": 56, "resumes_in": 56, "gate_passers": 2, "errors": 0, **counts}
    return {"ts": ts, "kind": "screen_completed", "actor": actor, "actor_role": "",
            "req_id": STEM,
            "summary": f"Screened {merged['scored']} candidates — IN hiring rules · "
                       f"checklist v2 · run {run_id}",
            "data": {"run_id": run_id, "pack": "IN", "counts": merged,
                     "duration_seconds": 3.2}}


def test_history_newest_first_with_counts_and_actors(client):
    _write_events([
        _completed("2026-07-15T09:00:00+00:00", "run-1", "ta@example.com"),
        {"ts": "2026-07-16T10:00:00+00:00", "kind": "screen_stopped",
         "actor": "head@example.com", "actor_role": "", "req_id": STEM,
         "summary": "Screening stopped by head@example.com after 10 of 56 résumés — "
                    "finished work is kept, screening again picks up where it left off",
         "data": {"job_id": "j2", "done": 10, "total": 56}},
        _completed("2026-07-17T11:00:00+00:00", "run-3", "ta@example.com",
                   gate_passers=3, errors=1),
        # another role's run must not leak in
        {"ts": "2026-07-17T12:00:00+00:00", "kind": "screen_completed",
         "actor": "x", "actor_role": "", "req_id": "another-role",
         "summary": "someone else's run", "data": {"run_id": "zzz"}},
    ])

    out = client.get(f"/api/roles/{STEM}/screen/runs",
                     headers=_as("TASpecialist")).json()
    runs = out["runs"]
    assert [r["kind"] for r in runs] == ["screen_completed", "screen_stopped",
                                         "screen_completed"]
    latest = runs[0]
    assert latest["run_id"] == "run-3"
    assert latest["actor"] == "ta@example.com"
    assert latest["counts"] == {"scored": 56, "resumes_in": 56,
                                "gate_passers": 3, "errors": 1}
    assert latest["duration_seconds"] == 3.2
    stopped = runs[1]
    assert "stopped by head@example.com" in stopped["summary"]
    assert "counts" not in stopped
    assert out["note"] is None  # nothing pre-spine to caveat


def test_pre_spine_run_meta_falls_back_to_one_honest_entry(client):
    out_dir = Path("output") / STEM
    out_dir.mkdir(parents=True)
    (out_dir / "run_meta.json").write_text(json.dumps({
        "run_id": "old-run", "started_utc": "2026-07-10T08:00:00+00:00",
        "policy_pack": "IN", "duration_seconds": 8.1,
        "counts": {"scored": 12, "resumes_in": 12, "gate_passers": 4, "errors": 0},
        "versions": {"rubric": f"{STEM}.rubric.v1.yaml (v1)"},
    }), encoding="utf-8")

    out = client.get(f"/api/roles/{STEM}/screen/runs",
                     headers=_as("TASpecialist")).json()
    [entry] = out["runs"]
    assert entry["kind"] == "screen_completed"
    assert entry["actor"] == ""  # pre-spine runs recorded no user
    assert entry["run_id"] == "old-run"
    assert entry["counts"]["gate_passers"] == 4
    assert "checklist v1" in entry["summary"]
    assert "before this history existed" in out["note"]


def test_no_runs_yet_is_empty_but_valid(client):
    out = client.get(f"/api/roles/{STEM}/screen/runs",
                     headers=_as("TASpecialist")).json()
    assert out == {"runs": [], "note": None}


def test_history_requires_recruiter(client):
    resp = client.get(f"/api/roles/{STEM}/screen/runs",
                      headers=_as("HiringManager"))
    assert resp.status_code == 403
