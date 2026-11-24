"""Private self-notes (2026-07-20): saveable without a decision, private to the
author, surviving a re-screen (keyed by candidate, not run)."""

from __future__ import annotations

from tests.test_staleness import _as, _mk_role, _mk_run, client, workspace  # noqa: F401


def test_self_note_store_is_private_and_run_independent(workspace):
    from resume_screener import self_notes

    self_notes.save_note("req", "cand-1", author="a@x", text="keep me")
    # keyed by (req, candidate, author) — NO run id, so a re-screen can't wipe it
    assert self_notes.get_note("req", "cand-1", "a@x")["text"] == "keep me"
    assert self_notes.get_note("req", "cand-1", "b@x") is None      # private to a@x
    assert self_notes.get_note("req", "cand-2", "a@x") is None      # per candidate
    # re-saving overwrites; empty clears
    self_notes.save_note("req", "cand-1", author="a@x", text="updated")
    assert self_notes.get_note("req", "cand-1", "a@x")["text"] == "updated"
    self_notes.save_note("req", "cand-1", author="a@x", text="  ")
    assert self_notes.get_note("req", "cand-1", "a@x") is None


def test_self_note_saves_without_a_decision_and_stays_private(workspace, client):
    req_id = _mk_role()
    cids = _mk_run(req_id)
    # saves on its own — no decision recorded
    r = client.put(f"/api/roles/{req_id}/candidates/{cids[0]}/note",
                   headers=_as("HRHead"), json={"text": "call their referee"})
    assert r.status_code == 200 and r.json()["text"] == "call their referee"

    mine = client.get(f"/api/roles/{req_id}/review/{cids[0]}",
                      headers=_as("HRHead")).json()
    assert mine["self_note"]["text"] == "call their referee"
    # a different recruiter (also reqs_all) does NOT see the private note
    others = client.get(f"/api/roles/{req_id}/review/{cids[0]}",
                        headers=_as("SuperAdmin")).json()
    assert others["self_note"] is None
    # and it wasn't recorded as a decision
    assert client.get(f"/api/roles/{req_id}/review",
                      headers=_as("HRHead")).json()["decision_counts"]["awaiting"] == 1
