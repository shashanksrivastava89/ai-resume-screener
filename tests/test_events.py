"""P29: the app-event spine (signals/events.jsonl) — Addendum 11 Part D2.

One JSON line per event, cross-process-locked writes, corrupt lines skipped on
read, req/kind filters. The governance-separation regression (req events never
flip review_pending) lives in test_req_lifecycle_api.py.
"""

from __future__ import annotations

import json
from datetime import datetime

from resume_screener.events import emit_event, load_events


def test_round_trip_shape(tmp_path):
    log = tmp_path / "events.jsonl"
    written = emit_event(
        "req_close", actor="head@example.com", actor_role="HRHead",
        req_id="finance-analyst", candidate_id="c1",
        summary="Closed — hired 1", data={"outcome": {"kind": "hired"}},
        log_path=log,
    )
    [loaded] = load_events(log_path=log)
    assert loaded == written
    assert loaded["kind"] == "req_close"
    assert loaded["actor"] == "head@example.com"
    assert loaded["actor_role"] == "HRHead"
    assert loaded["req_id"] == "finance-analyst"
    assert loaded["candidate_id"] == "c1"
    assert loaded["summary"] == "Closed — hired 1"
    assert loaded["data"] == {"outcome": {"kind": "hired"}}
    # ts is UTC ISO-8601 and parseable
    assert datetime.fromisoformat(loaded["ts"]).utcoffset().total_seconds() == 0


def test_optional_ids_are_omitted_not_null(tmp_path):
    log = tmp_path / "events.jsonl"
    emit_event("flag_changed", actor="it@example.com", summary="x", log_path=log)
    [entry] = load_events(log_path=log)
    assert "req_id" not in entry and "candidate_id" not in entry
    assert entry["data"] == {}


def test_req_and_kind_filters(tmp_path):
    log = tmp_path / "events.jsonl"
    emit_event("req_close", actor="a", req_id="r1", log_path=log)
    emit_event("req_snooze", actor="a", req_id="r1", log_path=log)
    emit_event("req_close", actor="a", req_id="r2", log_path=log)
    emit_event("flag_changed", actor="a", log_path=log)  # no req_id

    assert len(load_events(log_path=log)) == 4
    assert [e["kind"] for e in load_events(req_id="r1", log_path=log)] == [
        "req_close", "req_snooze"]
    assert len(load_events(kinds=["req_close"], log_path=log)) == 2
    assert [e["req_id"] for e in load_events(req_id="r1", kinds=["req_close"],
                                             log_path=log)] == ["r1"]


def test_corrupt_lines_are_skipped(tmp_path):
    log = tmp_path / "events.jsonl"
    emit_event("req_close", actor="a", req_id="r1", log_path=log)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("{half a line\n")
        handle.write('"a bare string"\n')
        handle.write("\n")
    emit_event("req_reopen", actor="a", req_id="r1", log_path=log)
    kinds = [e["kind"] for e in load_events(log_path=log)]
    assert kinds == ["req_close", "req_reopen"]


def test_missing_log_reads_empty(tmp_path):
    assert load_events(log_path=tmp_path / "nope.jsonl") == []


def test_write_takes_the_lock_sidecar(tmp_path):
    log = tmp_path / "events.jsonl"
    emit_event("req_close", actor="a", req_id="r1", log_path=log)
    # locking.file_lock locks a <name>.lock SIDECAR, never the data file itself
    assert (tmp_path / "events.jsonl.lock").exists()
