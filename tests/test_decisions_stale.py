"""P51 decision gating: NEW decisions on a stale run 409 with code stale_run
(single + batch); `clear` stays allowed; a fresh run is unaffected; and the
decisions_superseded event fires only when a re-run replaces decided scores."""

from __future__ import annotations

import pytest

from resume_screener import governance
from resume_screener.events import load_events
from tests.test_staleness import _as, _mk_role, _mk_run, _publish_bands, client, workspace  # noqa: F401


def test_single_and_batch_409_on_stale_run(workspace, client):
    req_id = _mk_role()
    cids = _mk_run(req_id, cids=("c1", "c2"))
    _publish_bands(85)

    r = client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": cids[0], "action": "forward"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "stale_run"
    assert "scoring model" in detail["message"]
    assert "Re-screen" in detail["message"]
    assert detail["stale"]["changed"] == ["scoring_model"]

    rb = client.post(f"/api/roles/{req_id}/decisions/batch", headers=_as("HRHead"),
                     json={"candidate_ids": cids, "action": "discuss"})
    assert rb.status_code == 409


def test_clear_is_allowed_on_stale_run(workspace, client):
    req_id = _mk_role()
    cids = _mk_run(req_id)
    # decide while fresh, then drift, then clear — clearing must not be trapped
    assert client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                       json={"candidate_id": cids[0], "action": "forward"}).status_code == 200
    _publish_bands(85)
    r = client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": cids[0], "action": "clear"})
    assert r.status_code == 200


def test_fresh_run_decides_normally(workspace, client):
    req_id = _mk_role()
    cids = _mk_run(req_id)
    r = client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": cids[0], "action": "forward"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_decisions_superseded_event(workspace):
    """The event helper: fires only when the run_id moved AND decisions existed."""
    from resume_screener.api import jobs

    req_id = _mk_role()
    cids = _mk_run(req_id, run_id="run-1")

    # simulate the runner's capture-then-emit sequence
    jobs._live_update("job-x", prior_run=jobs._prior_run_state(req_id))
    prior = jobs._LIVE["job-x"]["prior_run"]
    assert prior == {"run_id": "run-1", "decisions": 0}
    jobs._emit_decisions_superseded(req_id, "hr@x", "job-x")
    assert not [e for e in load_events() if e["kind"] == "decisions_superseded"]

    # with a decision on record + a new run_id, the event fires
    from resume_screener.feedback import record_decision
    from resume_screener.models import Decision

    record_decision(Decision(
        candidate_id=cids[0], run_id="run-1", suggestion="Possible (70)",
        human_action="agree", final_band="Strong", reason="Move forward",
        reviewer="hr@x", versions={}, ts="2026-07-17T01:00:00+00:00"))
    jobs._live_update("job-y", prior_run=jobs._prior_run_state(req_id))
    assert jobs._LIVE["job-y"]["prior_run"]["decisions"] == 1

    import json
    from pathlib import Path

    meta_path = Path("output") / req_id / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["run_id"] = "run-2"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    jobs._emit_decisions_superseded(req_id, "hr@x", "job-y")
    events = [e for e in load_events() if e["kind"] == "decisions_superseded"]
    assert len(events) == 1
    assert events[0]["data"] == {"old_run_id": "run-1", "new_run_id": "run-2",
                                 "decisions": 1}
    assert "carried forward" in events[0]["summary"]


def _rescreen_to(req_id, run_id, service):
    """Simulate a re-screen: same candidate records, a new run_id, re-stamped
    under the CURRENTLY published scoring model."""
    import json
    from pathlib import Path

    meta_path = Path("output") / req_id / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["run_id"] = run_id
    meta["versions"]["scoring_model"] = service.current_config_stamps(
        service.config())["scoring_model"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_decisions_carry_forward_with_reconfirm(workspace, client):
    """A re-screen must NOT wipe decisions (2026-07-20): they carry forward, one
    made under scores that have since moved shows needs_reconfirm, and
    reconfirming re-records it on the new run and clears the badge — the note is
    preserved throughout."""
    from resume_screener.api import service

    req_id = _mk_role()
    cids = _mk_run(req_id, cids=("c1",), run_id="run-1")
    assert client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                       json={"candidate_id": cids[0], "action": "forward",
                             "reason": "Strong GL background"}).status_code == 200

    _publish_bands(85)                     # the model moves …
    _rescreen_to(req_id, "run-2", service)  # … then a re-screen lands new scores

    # the decision + its note survived the re-screen
    recs = service.role_decision_records(req_id)
    assert cids[0] in recs and recs[cids[0]].reason == "Strong GL background"
    meta = service.read_run_meta(req_id)
    assert service.decision_reconfirm(recs[cids[0]], meta)["needs_reconfirm"] is True

    review = client.get(f"/api/roles/{req_id}/review", headers=_as("HRHead")).json()
    card = next(c for c in review["cards"] if c["candidate_id"] == cids[0])
    assert card["decided"] == "forward" and card["decided_reconfirm"] is True
    assert review["staleness"] is None  # run-2 stamped the current model → fresh

    rc = client.post(f"/api/roles/{req_id}/candidates/{cids[0]}/reconfirm",
                     headers=_as("HRHead"))
    assert rc.status_code == 200
    recs2 = service.role_decision_records(req_id)
    assert service.decision_reconfirm(recs2[cids[0]], meta)["needs_reconfirm"] is False
    assert recs2[cids[0]].reason == "Strong GL background"


def test_carry_forward_is_scoped_to_the_same_req(workspace, client):
    """A résumé shared across requisitions has the SAME candidate_id — a decision
    on one req must NOT carry into a FRESH other req (2026-07-21 regression: a
    fresh req was showing carried decisions + a reconfirm banner)."""
    from resume_screener.api import service

    req_a = _mk_role(stem="req-a", title="Req A")
    ids_a = _mk_run(req_a, cids=("shared",), run_id="run-a")
    assert client.post(f"/api/roles/{req_a}/decisions", headers=_as("HRHead"),
                       json={"candidate_id": ids_a[0], "action": "forward"}).status_code == 200

    # a DIFFERENT req, freshly screened with the SAME résumé content
    req_b = _mk_role(stem="req-b", title="Req B")
    ids_b = _mk_run(req_b, cids=("shared",), run_id="run-b")
    assert ids_a[0] == ids_b[0]  # same bytes → same candidate_id

    # req B is genuinely fresh: nothing carried from req A, no reconfirm/stale
    assert service.role_decision_records(req_b) == {}
    req = next(r for r in service.requisitions(service.config()) if r.req_id == req_b)
    review = __import__("resume_screener.api.review", fromlist=["build_review"]).build_review(req)
    assert review["decision_counts"]["awaiting"] == 1
    assert all(not c["decided"] for c in review["cards"])
    assert review["staleness"] is None


def test_reconfirm_all_only_touches_moved_decisions(workspace, client):
    from resume_screener.api import service

    req_id = _mk_role()
    cids = _mk_run(req_id, cids=("c1", "c2"), run_id="run-1")
    for c in cids:
        client.post(f"/api/roles/{req_id}/decisions", headers=_as("HRHead"),
                    json={"candidate_id": c, "action": "forward"})
    _publish_bands(85)
    _rescreen_to(req_id, "run-2", service)
    r = client.post(f"/api/roles/{req_id}/decisions/reconfirm-all", headers=_as("HRHead"))
    assert r.status_code == 200 and r.json()["reconfirmed"] == 2
    # a second call re-records nothing (all now current)
    r2 = client.post(f"/api/roles/{req_id}/decisions/reconfirm-all", headers=_as("HRHead"))
    assert r2.json()["reconfirmed"] == 0
