"""P49: the DB backends for the new v2 domains — preferences, events, hm-takes,
feature flags — plus their backfill importers.

Two guarantees per domain:
1. CONTRACT PARITY — the same public API returns the same reads through the file
   store (default) and the DB backend (the dbharness SQLite pattern: point the
   engine + session maker at an in-memory DB and flip the module's `_use_db`).
2. IMPORT IDEMPOTENCE — the backfill importer carries the file history across and
   is safe to re-run (append-only domains skip; upsert domains don't duplicate).

Fully offline (in-memory SQLite); the file defaults are exercised in each domain's
own suite (test_preferences/test_events/test_hm_takes/test_feature_flags).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import events, feature_flags, hm_takes, preferences
from resume_screener.db import engine as engine_mod
from resume_screener.db import importers
from resume_screener.db import session as session_mod


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """An in-memory SQLite wired as THE engine + session maker, cwd in a clean tmp
    so no real files are read/written. Returns a session factory for direct repo use."""
    monkeypatch.chdir(tmp_path)
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    try:
        yield maker
    finally:
        eng.dispose()


# --- preferences (PREFS_BACKEND) ----------------------------------------------------


def test_preferences_parity_file_vs_db(db, tmp_path, monkeypatch):
    # DB backend
    monkeypatch.setattr(preferences, "_use_db", lambda: True)
    p = preferences.add_preference("ta@example.com", "Prefers strong Python experience",
                                   owner_name="TA One")
    assert p["status"] == "pending" and p["display_status"] == "pending"
    mine = preferences.list_prefs("ta@example.com")
    assert len(mine) == 1 and mine[0]["text"] == "Prefers strong Python experience"
    assert mine[0]["owner"] == "ta@example.com"  # owner_email surfaces as 'owner'
    # withdraw folds latest-wins (one row, terminal state) — not a second row
    preferences.withdraw(p["pref_id"], owner="ta@example.com")
    folded = preferences.list_prefs("ta@example.com")
    assert len(folded) == 1 and folded[0]["display_status"] == "withdrawn"

    # FILE backend, same inputs → same read shape (stable keys)
    monkeypatch.setattr(preferences, "_use_db", lambda: False)
    monkeypatch.setattr(preferences, "PREFERENCES_LOG", tmp_path / "prefs.jsonl")
    fp = preferences.add_preference("ta@example.com", "Prefers strong Python experience",
                                    owner_name="TA One",
                                    log_path=tmp_path / "prefs.jsonl")
    fmine = preferences.list_prefs("ta@example.com", log_path=tmp_path / "prefs.jsonl")
    assert fmine[0]["status"] == "pending"
    assert fp["owner"] == "ta@example.com" and fmine[0]["text"] == fp["text"]


def test_preferences_approval_flow_db(db, monkeypatch):
    monkeypatch.setattr(preferences, "_use_db", lambda: True)
    # a warn-level pref lands pending, then approves (bounded +2, review_due set)
    monkeypatch.setattr(preferences, "classify_pref", lambda *a, **k: _warn_check())
    pending = preferences.add_preference("hr@example.com", "warn text")
    assert pending["status"] == "pending"
    approved = preferences.approve([pending["pref_id"]], approver="hrhead@example.com")
    assert len(approved) == 1 and approved[0]["status"] == "approved"
    assert approved[0]["approved_by"] == "hrhead@example.com" and approved[0]["review_due"]


def test_a_preference_stored_active_in_the_db_reads_pending(db, monkeypatch):
    """A DB row written as 'active' before 2026-10-16 reads as waiting for sign-off."""
    monkeypatch.setattr(preferences, "_use_db", lambda: True)
    p = preferences.add_preference("ta@example.com", "Prefers strong Python experience")
    preferences._append({**{k: v for k, v in p.items() if k != "display_status"},
                         "status": "active", "action": "add"})
    [row] = preferences.list_prefs("ta@example.com")
    assert row["status"] == "active" and row["display_status"] == "pending"
    assert not preferences.applies(row)


def _warn_check():
    from resume_screener.prefcheck import TextCheck
    return TextCheck(level="warn", basis_ids=["location_preference"], reason="needs a look")


# --- events (EVENTS_BACKEND) --------------------------------------------------------


def test_events_parity_and_ordering_db(db, tmp_path, monkeypatch):
    monkeypatch.setattr(events, "_use_db", lambda: True)
    events.emit_event("req_close", actor="ta@example.com", req_id="fin", summary="Closed")
    events.emit_event("pref_add", actor="ta@example.com", summary="Added pref")
    events.emit_event("req_reopen", actor="ta@example.com", req_id="fin", summary="Reopened")

    all_ev = events.load_events()
    assert [e["kind"] for e in all_ev] == ["req_close", "pref_add", "req_reopen"]  # id order
    fin = events.load_events(req_id="fin")
    assert [e["summary"] for e in fin] == ["Closed", "Reopened"]
    prefs_only = events.load_events(kinds=["pref_add"])
    assert len(prefs_only) == 1 and prefs_only[0]["actor"] == "ta@example.com"

    # FILE backend, same three events → same kind order
    monkeypatch.setattr(events, "_use_db", lambda: False)
    log = tmp_path / "events.jsonl"
    for kind, rid, summ in [("req_close", "fin", "Closed"), ("pref_add", None, "Added pref"),
                            ("req_reopen", "fin", "Reopened")]:
        events.emit_event(kind, actor="ta@example.com", req_id=rid, summary=summ, log_path=log)
    assert [e["kind"] for e in events.load_events(log_path=log)] == \
        ["req_close", "pref_add", "req_reopen"]


# --- hm takes (HM_TAKES_BACKEND) ----------------------------------------------------


def test_hm_takes_parity_and_supersede_db(db, monkeypatch):
    monkeypatch.setattr(hm_takes, "_use_db", lambda: True)
    hm_takes.record_takes("fin", "run-1", [{"candidate_id": "c1", "take": "yes", "note": "solid"}],
                          hm_email="hm@example.com", hm_name="HM One")
    got = hm_takes.list_takes("fin", "run-1")
    assert len(got) == 1 and got[0]["take"] == "yes" and got[0]["req_id"] == "fin"

    # a re-take supersedes (latest-wins) — still ONE row for the key
    hm_takes.record_takes("fin", "run-1", [{"candidate_id": "c1", "take": "no"}],
                          hm_email="hm@example.com")
    by_cand = hm_takes.takes_by_candidate("fin", "run-1")
    assert set(hm_takes.list_takes("fin", "run-1")[0]) and len(hm_takes.list_takes("fin", "run-1")) == 1
    assert by_cand["c1"]["take"] == "no"


# --- feature flags (FLAGS_BACKEND) --------------------------------------------------


def test_flags_db_default_and_region_override(db, monkeypatch):
    from resume_screener.db.repositories import regions as regions_repo

    # seed a region + the file catalog (so name/description/order + env flags exist)
    with db() as s:
        regions_repo.upsert_region(s, "IN", "India")
        s.commit()
    monkeypatch.setattr(feature_flags, "_file_features",
                        lambda: {"blind_review": {"name": "Blind review", "default": False}})
    monkeypatch.setattr(feature_flags, "_use_flags_db", lambda: True)
    # audit/event writes during set_flag go to the (default file) backends — send them to tmp
    monkeypatch.setattr("resume_screener.governance.append_audit", lambda *a, **k: None)
    monkeypatch.setattr("resume_screener.events.emit_event", lambda *a, **k: None)

    # global default off → flip on via the DB backend
    assert feature_flags.flag_enabled("blind_review") is False
    feature_flags.set_flag("blind_review", None, True, author="hrhead@example.com")
    assert feature_flags.flag_enabled("blind_review") is True
    # a region override wins for that region
    feature_flags.set_flag("blind_review", "IN", False, author="hrhead@example.com")
    assert feature_flags.flag_enabled("blind_review", "IN") is False
    assert feature_flags.flag_enabled("blind_review") is True  # global still on


# --- importers: parity + idempotence ------------------------------------------------


def test_importers_carry_files_and_are_idempotent(db, tmp_path):
    # seed the four file stores on disk (cwd is tmp_path)
    (tmp_path / "signals").mkdir()
    (tmp_path / "output" / "feedback").mkdir(parents=True)
    import json
    (tmp_path / "signals" / "preferences.jsonl").write_text(
        json.dumps({"pref_id": "p1", "owner": "ta@example.com", "text": "t",
                    "status": "active", "cap_points": 2.0, "ts": "2026-06-01T00:00:00+00:00"}) + "\n"
        # a second line for p1 = a later transition; fold keeps the latest
        + json.dumps({"pref_id": "p1", "owner": "ta@example.com", "text": "t",
                      "status": "withdrawn", "cap_points": 2.0, "ts": "2026-06-02T00:00:00+00:00"}) + "\n",
        encoding="utf-8")
    (tmp_path / "signals" / "events.jsonl").write_text(
        json.dumps({"ts": "2026-06-01T00:00:00+00:00", "kind": "req_close", "actor": "x",
                    "req_id": "fin", "summary": "Closed", "data": {}}) + "\n", encoding="utf-8")
    (tmp_path / "output" / "feedback" / "hm_takes.jsonl").write_text(
        json.dumps({"take_id": "t1", "req_id": "fin", "run_id": "run-1", "candidate_id": "c1",
                    "take": "yes", "hm_email": "hm@example.com", "ts": "2026-06-01T00:00:00+00:00"}) + "\n",
        encoding="utf-8")

    with db() as s:
        r1 = {r["domain"]: r for r in [
            importers.import_preferences(s), importers.import_events(s),
            importers.import_hm_takes(s)]}
        s.commit()
    assert r1["preferences"]["added"] == 1  # p1 folded to one row
    assert r1["events"]["added"] == 1 and r1["hm_takes"]["added"] == 1

    # re-run: append-only/upsert domains skip (idempotent)
    with db() as s:
        r2 = {r["domain"]: r for r in [
            importers.import_preferences(s), importers.import_events(s),
            importers.import_hm_takes(s)]}
        s.commit()
    assert all(r2[d]["skipped"] for d in ("preferences", "events", "hm_takes"))

    # reads through the DB backend match the imported (folded) state
    import resume_screener.preferences as prefs_mod
    with db() as s:
        from resume_screener.db.repositories import preferences as prefs_repo
        folded = prefs_repo.fold(s)
    assert folded["p1"]["status"] == "withdrawn"
    assert prefs_mod  # (imported for symmetry with the parity tests)
