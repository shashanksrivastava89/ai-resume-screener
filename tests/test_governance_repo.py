"""Phase 3 tests — governance repository (audit log + review sign-off) + the
GOVERNANCE_BACKEND switch + the backfill importer. Fully offline (in-memory SQLite).

Three layers:
1. the governance repo directly (append-only trail, region scope, the pending-banner
   ordering that must survive same-second timestamps);
2. governance.record_review/review_pending/last_review/load_audit with
   GOVERNANCE_BACKEND=db, proving the file→DB switch preserves the banner semantics
   the file test (test_governance.py) pins down;
3. the importer carrying decisions.jsonl / audit_log.jsonl / review_status.json across.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import governance
from resume_screener.db import engine as engine_mod
from resume_screener.db import importers
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import governance as repo
from resume_screener.db.repositories import regions as regions_repo




@pytest.fixture
def session() -> Session:
    eng = _sqlite_engine()
    sess = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield sess
    finally:
        sess.close()
        eng.dispose()


# --- repo directly -----------------------------------------------------------------


def test_append_and_load_audit_roundtrip(session):
    repo.append_audit(session, {"action": "publish_registry", "target": "signals/registry.yaml",
                                "version": 2, "author": "hrhead", "rationale": "tune recency",
                                "summary": "skill_recency.cap_points→4.0", "ts": "2026-07-08T10:00:00+00:00"})
    session.commit()
    loaded = repo.load_audit(session)
    assert len(loaded) == 1
    e = loaded[0]
    assert e["action"] == "publish_registry" and e["version"] == 2
    assert e["author"] == "hrhead"  # actor column surfaces as the JSONL 'author' key
    assert e["summary"] == "skill_recency.cap_points→4.0"


def test_audit_is_append_only_and_ordered(session):
    for i in range(3):
        repo.append_audit(session, {"action": "publish_pack", "target": f"policies/IN.yaml",
                                    "version": i + 2, "author": "hrhead", "summary": f"edit {i}"})
    session.commit()
    versions = [e["version"] for e in repo.load_audit(session)]
    assert versions == [2, 3, 4]  # insertion order preserved


def test_record_review_then_last_review(session):
    repo.record_review(session, "HR Head; Counsel", "2026-07-07", "hrhead")
    session.commit()
    last = repo.last_review(session)
    assert last == {"ts": None, "reviewers": "HR Head; Counsel", "date": "2026-07-07", "by": "hrhead"}
    # record_review also writes the audit MARKER (ordering source for the banner).
    assert [e["action"] for e in repo.load_audit(session)] == ["review_signoff"]


def test_review_required_fields(session):
    with pytest.raises(ValueError, match="required"):
        repo.record_review(session, "   ", "2026-07-07", "hrhead")
    session.rollback()
    assert repo.last_review(session) is None


def test_pending_banner_clears_then_reopens(session):
    assert repo.review_pending(session) is True  # never reviewed → pending
    repo.record_review(session, "HR; Legal", "2026-07-07", "hrhead")
    session.commit()
    assert repo.review_pending(session) is False  # signed off
    repo.append_audit(session, {"action": "publish_registry", "target": "signals/registry.yaml",
                                "version": 3, "author": "hrhead", "summary": "tweak"})
    session.commit()
    assert repo.review_pending(session) is True  # a later publish reopens it


def test_pending_ordering_survives_same_timestamp(session):
    """The banner must key on ROW ORDER, not ts: a sign-off and a following publish
    can share a clock second. Give both the SAME ts and assert the publish still wins."""
    same_ts = "2026-07-08T09:00:00+00:00"
    repo.record_review(session, "HR; Legal", "2026-07-08", "hrhead", ts=same_ts)
    repo.append_audit(session, {"action": "publish_pack", "target": "policies/IN.yaml",
                                "version": 5, "author": "hrhead", "summary": "edit", "ts": same_ts})
    session.commit()
    assert repo.review_pending(session) is True  # later ROW wins despite equal ts


# --- region scope ------------------------------------------------------------------


def test_audit_region_scope_own_plus_global(session):
    regions_repo.upsert_region(session, "IN", "India")
    regions_repo.upsert_region(session, "US", "United States")
    session.flush()
    in_id = regions_repo.get_region_by_code(session, "IN").id
    us_id = regions_repo.get_region_by_code(session, "US").id
    repo.append_audit(session, {"action": "publish_pack", "summary": "IN edit"}, region_id=in_id)
    repo.append_audit(session, {"action": "publish_pack", "summary": "US edit"}, region_id=us_id)
    repo.append_audit(session, {"action": "publish_registry", "summary": "global edit"})  # region NULL
    session.commit()

    # A region sees its OWN entries + the global (NULL) ones, never another region's.
    in_summaries = {e["summary"] for e in repo.load_audit(session, region_id=in_id)}
    assert in_summaries == {"IN edit", "global edit"}
    # The whole-org view (region_id=None) sees everything.
    assert len(repo.load_audit(session)) == 3


def test_pending_banner_is_per_region(session):
    regions_repo.upsert_region(session, "IN", "India")
    regions_repo.upsert_region(session, "US", "United States")
    session.flush()
    in_id = regions_repo.get_region_by_code(session, "IN").id
    us_id = regions_repo.get_region_by_code(session, "US").id
    # Sign off IN only; then publish a change to US.
    repo.record_review(session, "IN HR; IN Legal", "2026-07-07", "hrhead", region_id=in_id)
    session.commit()
    repo.append_audit(session, {"action": "publish_pack", "summary": "US edit"}, region_id=us_id)
    session.commit()
    # IN stays signed off (the US change doesn't touch it); US is still pending.
    assert repo.review_pending(session, region_id=in_id) is False
    assert repo.review_pending(session, region_id=us_id) is True


# --- governance.py backend switch (file → db) --------------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    """Point governance at an in-memory SQLite via GOVERNANCE_BACKEND=db."""
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(governance, "_use_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


def test_switch_preserves_banner_semantics(db_backend):
    assert governance.review_pending() is True
    governance.record_review("HR Head; Counsel", "2026-07-07", "tester")
    assert governance.review_pending() is False
    assert governance.last_review()["reviewers"] == "HR Head; Counsel"
    governance.append_audit({"action": "publish_registry", "version": 3,
                             "author": "tester", "summary": "tweak"})
    assert governance.review_pending() is True  # a later change reopens it


def test_switch_record_review_reason_required(db_backend):
    with pytest.raises(ValueError, match="required"):
        governance.record_review("", "2026-07-07", "tester")
    assert governance.last_review() is None


def test_switch_load_audit_returns_ui_shape(db_backend):
    governance.append_audit({"action": "publish_pack", "target": "policies/IN.yaml",
                             "version": 4, "author": "tester", "rationale": "why",
                             "summary": "IN: obligations"})
    entries = governance.load_audit()
    assert len(entries) == 1
    # exactly the keys the React AuditEntry type reads
    for key in ("ts", "action", "target", "author", "rationale", "summary", "version"):
        assert key in entries[0]


def test_switch_pack_publish_stamps_governing_region(db_backend):
    """A pack publish resolves its jurisdiction to the governing region and stamps it,
    so that region's audit trail (and only it) carries the entry."""
    with session_mod.session_scope() as seed:  # same maker the switch uses
        regions_repo.upsert_region(seed, "APAC", "Asia-Pacific")
        regions_repo.upsert_jurisdiction(seed, "IN", "India", region_code="APAC")
    governance.append_audit({"action": "publish_pack", "target": "policies/IN.yaml",
                             "version": 5, "author": "tester", "summary": "IN edit"},
                            jurisdiction="IN")
    with session_mod.session_scope() as check:
        apac_id = regions_repo.get_region_by_code(check, "APAC").id
        apac_entries = repo.load_audit(check, region_id=apac_id)
        assert [e["summary"] for e in apac_entries] == ["IN edit"]
        assert apac_entries[0]["region_id"] == apac_id  # stamped, not global


# --- importer ----------------------------------------------------------------------


def test_import_all_backfills_every_domain(session, tmp_path, monkeypatch):
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(json.dumps({
        "candidate_id": "cid1", "run_id": "run-1", "suggestion": "Strong (94)",
        "human_action": "agree", "final_band": None, "reason": "solid treasury depth",
        "reviewer": "Shashank", "versions": {"policy_pack": "IN"}, "ts": "2026-07-06T10:00:00+00:00",
    }) + "\n", encoding="utf-8")
    audit = tmp_path / "audit_log.jsonl"
    audit.write_text(
        json.dumps({"ts": "2026-07-07T09:00:00+00:00", "action": "publish_registry",
                    "version": 2, "author": "hrhead", "summary": "edit"}) + "\n"
        + json.dumps({"ts": "2026-07-07T10:00:00+00:00", "action": "review_signoff",
                      "author": "hrhead", "summary": "reviewers: HR; Legal"}) + "\n",
        encoding="utf-8")
    review = tmp_path / "review_status.json"
    review.write_text(json.dumps({"ts": "2026-07-07T10:00:00+00:00", "reviewers": "HR; Legal",
                                  "date": "2026-07-07", "by": "hrhead"}), encoding="utf-8")

    r1 = importers.import_decisions(session, decisions)
    r2 = importers.import_audit(session, audit)
    r3 = importers.import_review(session, review)
    session.commit()

    assert r1 == {"domain": "decisions", "added": 1, "skipped": False, "reason": ""}
    assert r2["added"] == 2 and r3["added"] == 1
    # The review sign-off row is present exactly once (importer must NOT re-write the
    # audit marker it already imported from the JSONL).
    assert [e["action"] for e in repo.load_audit(session)] == ["publish_registry", "review_signoff"]
    assert repo.last_review(session)["reviewers"] == "HR; Legal"
    # Banner: last sign-off is the newest audit row → not pending.
    assert repo.review_pending(session) is False


def test_import_is_idempotent_when_table_not_empty(session, tmp_path):
    audit = tmp_path / "audit_log.jsonl"
    audit.write_text(json.dumps({"action": "publish_pack", "summary": "e"}) + "\n", encoding="utf-8")
    assert importers.import_audit(session, audit)["added"] == 1
    session.commit()
    again = importers.import_audit(session, audit)
    assert again["skipped"] is True and again["added"] == 0
    assert len(repo.load_audit(session)) == 1  # no duplicate append


def test_import_missing_files_are_skipped(session, tmp_path):
    reports = {r["domain"]: r for r in [
        importers.import_decisions(session, tmp_path / "nope.jsonl"),
        importers.import_audit(session, tmp_path / "nope.jsonl"),
        importers.import_review(session, tmp_path / "nope.json"),
    ]}
    assert all(r["skipped"] and r["reason"] == "no file" for r in reports.values())
