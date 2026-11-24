"""Stopping a screening run + the runner's live extras (ETA, per-candidate
progress, the interrupted-run sidecar).

Offline: the subprocess is a fake with a scripted stdout; the memory job store
is the default backend. The invariants pinned here:

- cancel intent is recorded BEFORE the kill, so a cancelled run always
  finalizes as "stopped" — never as a plain error;
- a run that already finished beats a last-moment stop click (its results
  landed — "done" wins);
- the per-candidate protocol lines ([candidate-done]/[candidate-error]) feed
  the job's `completed` list and stay out of the error tail;
- a sidecar left behind by a dead process becomes a screen_interrupted event
  (and is never re-reported).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.api import jobs
from resume_screener.events import load_events
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

STEM = "finance-analyst"


class FakeProc:
    """A scripted `screener run` subprocess. Items in `lines` are stdout lines,
    or callables to run mid-stream (e.g. trigger a cancel while 'running')."""

    def __init__(self, lines, exit_code=0, pid=4242, kill_changes_exit=True):
        self.pid = pid
        self.killed = False
        self._exit_code = exit_code
        self._kill_changes_exit = kill_changes_exit
        self.stdout = self._stream(lines)

    def _stream(self, lines):
        for item in lines:
            if callable(item):
                item()
            else:
                yield item + "\n"

    def kill(self):
        self.killed = True

    def wait(self):
        if self.killed and self._kill_changes_exit:
            return 1
        return self._exit_code


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A tmp cwd with a real role (so _req_authorized resolves), a fresh memory
    job store and clean live-state registries."""
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

    fresh = jobs._MemoryStore()
    monkeypatch.setattr(jobs, "_MEMORY", fresh)
    jobs._reset_live_state()
    yield fresh
    jobs._reset_live_state()


def _run_with(monkeypatch, store, fake, job_id="j1", req_id=STEM,
              actor="ta@example.com"):
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: fake)
    store.create(job_id, req_id)
    jobs._run(job_id, req_id, ["fake-cmd"], store, actor)


# --- cancel --------------------------------------------------------------------------


def test_cancel_mid_run_finalizes_as_stopped(workspace, monkeypatch):
    outcomes = {}

    def trigger_cancel():
        outcomes["cancel"] = jobs.cancel_job("j1", "head@example.com")[0]

    fake = FakeProc([
        "candidates:  33%|###       | 1/3 [00:01<00:02,  1.0it/s]",
        trigger_cancel,
        "candidates:  66%|######    | 2/3 [00:02<00:01,  1.0it/s]",
    ])
    _run_with(monkeypatch, workspace, fake)

    assert outcomes["cancel"] == "stopping"
    assert fake.killed
    job = jobs.get_job("j1")
    assert job["status"] == "stopped"
    assert "cancelled by head@example.com" in job["message"]
    assert "batch is unlocked" in job["message"]
    # a cancelled job unblocks re-screening AND batch changes immediately
    assert jobs.active_job_for(STEM) is None
    assert jobs.screen_lock(STEM) is None
    # …and is on the record, with the canceller as the actor
    [event] = load_events(req_id=STEM, kinds={"screen_stopped"})
    assert event["actor"] == "head@example.com"
    assert event["data"]["job_id"] == "j1"
    assert event["data"]["intent"] == "cancel"


# --- pause (2026-07-19): halt but keep the batch locked -------------------------------


def test_pause_mid_run_finalizes_as_paused_and_keeps_the_lock(workspace, monkeypatch):
    def trigger_pause():
        assert jobs.cancel_job("j1", "ta@example.com", intent="pause")[0] == "stopping"

    fake = FakeProc([
        "candidates:  33%|###       | 1/3 [00:01<00:02,  1.0it/s]",
        trigger_pause,
    ])
    _run_with(monkeypatch, workspace, fake)

    job = jobs.get_job("j1")
    assert job["status"] == "paused"
    assert "paused by ta@example.com" in job["message"]
    # not RUNNING (nothing to poll), but the batch stays locked by it
    assert jobs.active_job_for(STEM) is None
    assert jobs.screen_lock(STEM)["job_id"] == "j1"
    [event] = load_events(req_id=STEM, kinds={"screen_paused"})
    assert event["actor"] == "ta@example.com" and event["data"]["intent"] == "pause"


def test_cancelling_a_paused_job_releases_the_lock(workspace, monkeypatch):
    def trigger_pause():
        jobs.cancel_job("j1", "ta@example.com", intent="pause")

    _run_with(monkeypatch, workspace, FakeProc(["x 1/3 [00:01<00:02]", trigger_pause]))
    assert jobs.screen_lock(STEM) is not None

    # pausing a paused job is a no-op; cancelling finalizes it in place
    assert jobs.cancel_job("j1", "ta@example.com", intent="pause")[0] == "not_running"
    outcome, job = jobs.cancel_job("j1", "ta@example.com", intent="cancel")
    assert outcome == "cancelled" and job["status"] == "stopped"
    assert jobs.screen_lock(STEM) is None
    # the release is on the record too
    assert load_events(req_id=STEM, kinds={"screen_stopped"})


def test_completed_run_beats_last_moment_stop(workspace, monkeypatch):
    """Exit code 0 with a pending cancel = the run finished; results landed."""

    def late_cancel():
        jobs.cancel_job("j1", "head@example.com")

    fake = FakeProc([
        "candidates: 100%|##########| 2/2 [00:04<00:00,  2.0it/s]",
        "Scored 2/2 resumes in 4.0s (1 met every must-have; 0 errors).",
        late_cancel,
    ], exit_code=0, kill_changes_exit=False)
    _run_with(monkeypatch, workspace, fake)

    job = jobs.get_job("j1")
    assert job["status"] == "done"
    assert job["message"].startswith("Scored ")
    assert load_events(req_id=STEM, kinds={"screen_stopped"}) == []


def test_cancel_unknown_and_finished_jobs(workspace):
    assert jobs.cancel_job("nope", "x")[0] == "not_found"
    workspace.create("j2", STEM)
    workspace.save("j2", STEM, {"status": "done", "done": 2, "total": 2})
    assert jobs.cancel_job("j2", "x")[0] == "not_running"


def test_cancel_without_a_process_handle_is_not_here(workspace):
    # e.g. the job row says running but THIS process didn't spawn it
    workspace.create("j3", STEM)
    workspace.save("j3", STEM, {"status": "running", "done": 1, "total": 5})
    assert jobs.cancel_job("j3", "x")[0] == "not_here"


# --- the cancel route ----------------------------------------------------------------


def test_cancel_route_stops_a_running_job(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    workspace.create("j4", STEM)
    workspace.save("j4", STEM, {"status": "running", "done": 1, "total": 5})
    fake = FakeProc([])
    with jobs._LIVE_LOCK:
        jobs._PROCS["j4"] = fake

    client = TestClient(app)
    resp = client.post("/api/screen/jobs/j4/cancel", headers=_as("TASpecialist"))
    assert resp.status_code == 200, resp.text
    assert fake.killed  # the runner thread (not running here) does the finalize


def test_cancel_route_guards(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    client = TestClient(app)
    assert client.post("/api/screen/jobs/nope/cancel",
                       headers=_as("TASpecialist")).status_code == 404

    workspace.create("j5", STEM)
    workspace.save("j5", STEM, {"status": "done", "done": 2, "total": 2})
    resp = client.post("/api/screen/jobs/j5/cancel", headers=_as("TASpecialist"))
    assert resp.status_code == 409
    assert "already finished" in resp.json()["detail"]

    workspace.create("j6", STEM)
    workspace.save("j6", STEM, {"status": "running", "done": 0, "total": 5})
    resp = client.post("/api/screen/jobs/j6/cancel", headers=_as("TASpecialist"))
    assert resp.status_code == 409
    assert "another app instance" in resp.json()["detail"]

    # no recruiter capability -> no control over runs
    assert client.post("/api/screen/jobs/j6/cancel",
                       headers=_as("HiringManager")).status_code == 403


# --- live extras: per-candidate lines + ETA ------------------------------------------


def test_runner_collects_candidate_lines_and_eta(workspace, monkeypatch):
    seen = {}

    def check_live():
        job = jobs.get_job("j1")
        seen["eta"] = job["eta_seconds"]
        seen["completed"] = list(job["completed"])

    fake = FakeProc([
        "[candidate-done] alice.pdf",
        "candidates:  50%|#####     | 1/2 [00:03<00:05,  2.1it/s]",
        check_live,
        "[candidate-error] bob.pdf",
        "Scored 1/2 resumes in 3.2s (1 met every must-have; 1 errors).",
    ], exit_code=0)
    _run_with(monkeypatch, workspace, fake)

    assert seen == {"eta": 5, "completed": ["alice.pdf"]}
    job = jobs.get_job("j1")
    assert job["status"] == "done"
    assert job["completed"] == ["alice.pdf", "bob.pdf"]
    assert job["eta_seconds"] is None  # cleared on finalize
    assert job["actor"] == ""  # actor rides _LIVE only when start_screening set it
    assert job["message"].startswith("Scored ")


def test_glued_candidate_markers_still_count(workspace, monkeypatch):
    """The real failure mode: tqdm's newline-less bar glues the per-candidate
    marker onto the progress line. Both the count AND the marker must register."""
    fake = FakeProc([
        "candidates:  50%|#####     | 1/2 [00:03<00:05,  2.1it/s][candidate-done] Anita Rao.pdf",
        "candidates: 100%|##########| 2/2 [00:06<00:00,  2.0it/s][candidate-done] Vikram Shah.pdf",
        "Scored 2/2 resumes in 6.0s (2 met every must-have; 0 errors).",
    ], exit_code=0)
    _run_with(monkeypatch, workspace, fake)

    job = jobs.get_job("j1")
    assert job["status"] == "done"
    assert (job["done"], job["total"]) == (2, 2)
    assert job["completed"] == ["Anita Rao.pdf", "Vikram Shah.pdf"]


def test_stopped_run_is_reattachable_with_its_completed_set(workspace, monkeypatch):
    """After a partial stop, active_job_for is None (unblocks re-screening) but
    latest_job_for still returns the stopped run WITH the résumés it finished —
    so the Candidates tab can re-show its panel across a reload / user switch."""
    def trigger_cancel():
        jobs.cancel_job("j1", "head@example.com")

    # The kill closes the pipe, so no further lines arrive after the cancel —
    # the run finalizes at 1 of 2, having screened just the first résumé.
    fake = FakeProc([
        "candidates:  50%|#####     | 1/2 [00:03<00:05,  2.1it/s][candidate-done] Anita Rao.pdf",
        trigger_cancel,
    ])
    _run_with(monkeypatch, workspace, fake)

    assert jobs.active_job_for(STEM) is None
    latest = jobs.latest_job_for(STEM)
    assert latest["status"] == "stopped"
    assert latest["done"] == 1 and latest["total"] == 2
    assert latest["completed"] == ["Anita Rao.pdf"]


def test_screen_active_route_reattaches_a_stopped_run(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    workspace.create("j8", STEM)
    workspace.save("j8", STEM, {"status": "stopped", "done": 10, "total": 66,
                                "message": "Screening stopped by head@example.com."})

    client = TestClient(app)
    out = client.get(f"/api/roles/{STEM}/screen/active", headers=_as("TASpecialist")).json()
    assert out["job"] is None  # nothing running
    assert out["last"]["status"] == "stopped"
    assert (out["last"]["done"], out["last"]["total"]) == (10, 66)


def test_screen_active_route_omits_a_completed_run(workspace):
    """A finished run is left to the preflight CTA + run history — `last` only
    re-surfaces runs that didn't finish (stopped/error)."""
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    workspace.create("j9", STEM)
    workspace.save("j9", STEM, {"status": "done", "done": 66, "total": 66})

    client = TestClient(app)
    out = client.get(f"/api/roles/{STEM}/screen/active", headers=_as("TASpecialist")).json()
    # the route still returns the latest job; the CLIENT ignores a done `last`
    assert out["job"] is None
    assert out["last"]["status"] == "done"


def test_candidate_lines_stay_out_of_the_error_tail(workspace, monkeypatch):
    fake = FakeProc([
        "[candidate-done] alice.pdf",
        "✗ Azure OpenAI is unreachable",
    ], exit_code=2)
    _run_with(monkeypatch, workspace, fake)

    job = jobs.get_job("j1")
    assert job["status"] == "error"
    assert job["error"] == "Azure OpenAI is unreachable"
    [event] = load_events(req_id=STEM, kinds={"screen_failed"})
    assert "didn't finish" in event["summary"]


def test_start_screening_stamps_live_extras(workspace, monkeypatch):
    """start_screening seeds actor/planned; the runner thread is stubbed out."""
    monkeypatch.setattr(jobs.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    job = jobs.start_screening(STEM, f"jds/{STEM}.md", actor="ta@example.com")
    assert job["actor"] == "ta@example.com"
    assert job["started_utc"]
    assert job["planned"] == []  # no batch folder in this workspace
    assert job["completed"] == []


# --- the interrupted-run sidecar -----------------------------------------------------


def test_run_writes_and_clears_the_sidecar(workspace, monkeypatch):
    sidecar = Path("output") / STEM / ".screen_job.json"
    seen = {}

    def check_sidecar():
        seen["exists"] = sidecar.exists()
        seen["pid"] = json.loads(sidecar.read_text(encoding="utf-8"))["pid"]

    fake = FakeProc([check_sidecar], exit_code=0)
    _run_with(monkeypatch, workspace, fake)
    assert seen == {"exists": True, "pid": 4242}
    assert not sidecar.exists()  # cleaned up on finalize


def test_reap_orphans_records_interruption(workspace):
    sidecar = Path("output") / STEM / ".screen_job.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({
        "job_id": "j9", "pid": 111, "actor": "ta@example.com",
        "started_utc": "2026-07-17T09:00:00+00:00",
    }), encoding="utf-8")

    assert jobs.reap_orphans() == 1
    assert not sidecar.exists()
    [event] = load_events(req_id=STEM, kinds={"screen_interrupted"})
    assert "ta@example.com" in event["summary"]
    assert "app restart" in event["summary"]
    # a second sweep finds nothing — no duplicate history entries
    assert jobs.reap_orphans() == 0
