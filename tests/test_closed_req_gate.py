"""A closed requisition takes no new hiring work (2026-10-16, pen-test RS-06).

Closing used to change only a label: the tester screened, decided, uploaded and
shared on a closed requisition. Now every hiring-work route answers 409 with
code "closed", closing stops a running or paused screen and any preparation, and
the auto-screen watchdog refuses. Reading, comments, notes, the iCIMS details,
stopping work and deleting files stay open — closing freezes the hiring, not the
record. Offline: tmp_path workspace + TestClient/_as().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_screener import batch_prep
from resume_screener.api import jobs, service
from resume_screener.ats.local_folder import save_req_meta
from tests.test_hm_share import _mk_screened_role, _suggest, client, workspace  # noqa: F401
from tests.test_sso_identity import _as

OFF = "This requisition is closed, so it can't be changed. Reopening is switched off — ask IT if it needs reopening."
ON = "This requisition is closed, so it can't be changed. Reopen it from History on the Requisitions page to continue."


@pytest.fixture(autouse=True)
def fresh_jobs(monkeypatch):
    monkeypatch.setattr(jobs, "_MEMORY", jobs._MemoryStore())
    jobs._reset_live_state()
    yield
    jobs._reset_live_state()


def _close(stem, kind="hired"):
    outcome = {"kind": kind, **({"hired_count": 1} if kind == "hired" else {})}
    save_req_meta(Path("jds") / f"{stem}.md", status="closed", outcome=outcome,
                  closed_at="2026-10-16T09:00:00+00:00", closed_by="taspecialist@example.com")


def _tree() -> dict[str, int]:
    return {str(p): p.stat().st_size for root in ("jds", "data", "output")
            for p in Path(root).rglob("*") if p.is_file()}


def _gated(stem, cid, sid):
    ta, hr, hm = _as("TASpecialist"), _as("HRHead"), _as("HiringManager")
    row = {"text": "GL accounting", "kind": "must_have", "importance": 3}
    return [
        ("POST", f"/api/roles/{stem}/screen", ta, {"json": {}}),
        ("POST", f"/api/roles/{stem}/decisions", ta,
         {"json": {"candidate_id": cid, "action": "forward"}}),
        ("POST", f"/api/roles/{stem}/decisions/batch", ta,
         {"json": {"candidate_ids": [cid], "action": "forward"}}),
        ("POST", f"/api/roles/{stem}/candidates/{cid}/reconfirm", ta, {"json": {}}),
        ("POST", f"/api/roles/{stem}/decisions/reconfirm-all", ta, {"json": {}}),
        ("POST", f"/api/roles/{stem}/batch/upload", ta,
         {"files": [("files", ("late.pdf", b"%PDF-1.4 late", "application/pdf"))]}),
        ("POST", f"/api/roles/{stem}/batch/import", ta, {"json": {"folder": "data/resumes"}}),
        ("POST", f"/api/roles/{stem}/batch/prepare", ta, {}),
        ("POST", f"/api/roles/{stem}/share-hm", ta, {}),
        ("POST", f"/api/roles/{stem}/rubric/draft", ta, {}),
        ("PUT", f"/api/roles/{stem}/rubric", ta, {"json": {"requirements": [row]}}),
        ("POST", f"/api/roles/{stem}/checklist-approval/request", ta, {}),
        ("POST", f"/api/roles/{stem}/checklist-approval/approve", hr, {"json": {}}),
        ("PUT", f"/api/roles/{stem}/hm", ta, {"json": {"email": "someone@example.com"}}),
        ("POST", f"/api/roles/{stem}/request-hm-review", ta, {}),
        ("POST", f"/api/roles/{stem}/suggestions", hm,
         {"json": {"requirement_id": "r01", "old_text": "GL accounting", "new_text": "GL"}}),
        ("POST", f"/api/suggestions/{sid}/accept", ta, {"json": {}}),
        ("POST", f"/api/suggestions/{sid}/decline", ta, {"json": {"note": "no"}}),
        ("POST", f"/api/roles/{stem}/takes", hm, {"json": {}}),
        ("POST", f"/api/roles/{stem}/takes/send", hm, {}),
        ("POST", f"/api/roles/{stem}/snooze", ta, {"json": {}}),
    ]


@pytest.mark.parametrize("kind", ["hired", "cancelled"])
def test_every_hiring_route_refuses_a_closed_requisition(client, kind):
    stem, ids = _mk_screened_role()
    sid = _suggest(client, stem).json()["suggestion_id"]
    _close(stem, kind)
    before = _tree()
    for method, path, headers, kw in _gated(stem, ids[0], sid):
        r = client.request(method, path, headers=headers, **kw)
        assert r.status_code == 409, (path, r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["code"] == "closed" and detail["message"] == ON, path
        assert detail["outcome"]["kind"] == kind
    assert _tree() == before  # nothing was written


def _set_flag(key: str, on: bool) -> None:
    import yaml

    path = Path("signals/features.yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["features"][key]["default"] = on
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_the_message_says_so_when_reopening_is_switched_off(client):
    stem, ids = _mk_screened_role()
    _close(stem)
    _set_flag("icims_reopen", False)
    r = client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": ids[0], "action": "forward"})
    assert r.status_code == 409 and r.json()["detail"]["message"] == OFF


def test_after_reopening_the_work_carries_on(client):
    stem, ids = _mk_screened_role()
    _close(stem)
    assert client.post(f"/api/roles/{stem}/reopen", headers=_as("TASpecialist")).status_code == 200
    again = client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                        json={"candidate_id": ids[0], "action": "forward"})
    assert again.status_code == 200, again.text


def test_the_record_stays_open_on_a_closed_requisition(client):
    stem, ids = _mk_screened_role()
    _close(stem)
    ta = _as("TASpecialist")
    assert client.get(f"/api/roles/{stem}/review", headers=ta).status_code == 200
    assert client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments", headers=ta,
                       json={"text": "for the file"}).status_code == 200
    assert client.put(f"/api/roles/{stem}/candidates/{ids[0]}/note", headers=ta,
                      json={"text": "mine"}).status_code == 200
    assert client.put(f"/api/roles/{stem}/details", headers=ta,
                      json={"icims_req_id": "REQ-corrected"}).status_code == 200
    assert client.post(f"/api/roles/{stem}/batch/prepare/stop", headers=ta).status_code == 200
    assert client.delete(f"/api/roles/{stem}/batch/resume-2.pdf", headers=ta).status_code == 200


# --- closing stops the work ---------------------------------------------------------


def _job(stem, status, job_id="j1"):
    jobs._MEMORY.create(job_id, stem)
    jobs._MEMORY.save(job_id, stem, {"status": status, "done": 1, "total": 3,
                                     "message": "…", "error": ""})
    jobs._live_update(job_id, actor="someone-else@example.com")
    return job_id


def _close_via_api(client, stem):
    r = client.post(f"/api/roles/{stem}/close", headers=_as("TASpecialist"),
                    json={"outcome": "cancelled"})
    assert r.status_code == 200, r.text
    return r.json()


def test_closing_cancels_a_paused_screen_whoever_started_it(client):
    stem, _ids = _mk_screened_role()
    _job(stem, "paused")
    body = _close_via_api(client, stem)
    assert body["stopped"]["screening"] is True and "note" not in body
    assert jobs.screen_lock(stem) is None
    from resume_screener.events import load_events

    close = load_events(req_id=stem, kinds=["req_close"])[-1]
    assert "stopped the running screen" in close["summary"]


def test_a_run_this_process_cannot_reach_is_named(client):
    stem, _ids = _mk_screened_role()
    _job(stem, "running")  # no subprocess handle here -> "not_here"
    body = _close_via_api(client, stem)
    assert body["stopped"]["screening"] is False
    assert "started elsewhere" in body["note"]
    assert service.is_closed(service.find_requisition(stem))


def test_closing_stops_preparation_and_the_run_it_owed(client, monkeypatch):
    monkeypatch.setenv("BATCH_PREP_INLINE", "0")
    monkeypatch.setattr(batch_prep, "_spawn", lambda *a, **k: None, raising=False)
    stem, _ids = _mk_screened_role()
    folder = service.batch_folder(service.config(), service.find_requisition(stem))
    batch_prep.stage_bytes("late.pdf", b"%PDF-1.4 late", folder)
    batch_prep.set_auto_screen(folder, actor="taspecialist@example.com")
    body = _close_via_api(client, stem)
    assert body["stopped"]["preparing"] is True
    state = batch_prep.read_state(folder)
    assert state["auto_screen"] is False and state["stop_requested"] is True


def test_closing_forgets_a_run_owed_after_preparation_finished(client):
    stem, _ids = _mk_screened_role()
    folder = service.batch_folder(service.config(), service.find_requisition(stem))
    batch_prep.set_auto_screen(folder, actor="taspecialist@example.com")
    body = _close_via_api(client, stem)
    assert body["stopped"] == {"screening": False, "preparing": False}
    state = batch_prep.read_state(folder)
    assert state["auto_screen"] is False and "closed" in state["message"]


def test_the_watchdog_will_not_screen_a_closed_requisition(client):
    stem, _ids = _mk_screened_role()
    _close(stem)
    reason = batch_prep._screen_blocked_reason(service.config(), service.find_requisition(stem))
    assert reason == "This requisition is closed, so screening didn't start."
