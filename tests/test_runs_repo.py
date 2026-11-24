"""Phase 4 tests — run-index repository + the RUN_INDEX_BACKEND read path + the
backfill importer. Fully offline (in-memory SQLite, no Postgres, no Azure).

Three layers:
1. the runs repo directly (record/list/latest, idempotent-by-run_id, version parsing,
   the run_meta reconstruction, region scope);
2. service.list_runs / service.read_run_meta with run_index_backend=db, proving the
   file→DB switch keeps the /api/runs shape and gives read_run_meta a DB fallback;
3. the importer scanning output/<req>/run_meta.json (per-run_id guard).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener.api import service
from resume_screener.db import engine as engine_mod
from resume_screener.db import importers
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import regions as regions_repo
from resume_screener.db.repositories import runs as repo


def _meta(run_id="accountant-role-20260709-101500", jurisdiction="IN", scored=4, strong=2) -> dict:
    return {
        "run_id": run_id,
        "role_title": "Senior Accountant",
        "jurisdiction": jurisdiction,
        "policy_pack": jurisdiction,
        "started_utc": "2026-07-09T10:15:00+00:00",
        "versions": {"rubric": "accountant.rubric.v2.yaml (v2)", "registry": "v5 (abc123def456)"},
        "counts": {"scored": scored, "gate_passers": 3, "errors": 0},
        "bands": {"Strong": strong, "Possible": 1, "Not a fit": 1},
        "obligations": ["DPDP notice at collection"],
        "cost_estimate": {"estimated_usd": 0.42, "chat_calls": 20},
    }




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


def test_record_and_list_run(session):
    repo.record_run(session, _meta(), req_code="accountant-role")
    session.commit()
    runs = repo.list_runs(session)
    assert len(runs) == 1
    r = runs[0]
    assert r.run_id == "accountant-role-20260709-101500"
    assert r.req_code == "accountant-role"
    assert r.role_title == "Senior Accountant"
    assert r.jurisdiction == "IN" and r.status == "complete"
    assert r.cost_estimate == 0.42


def test_version_stamps_parsed(session):
    repo.record_run(session, _meta(), req_code="accountant-role")
    session.commit()
    r = repo.list_runs(session)[0]
    assert r.rubric_version == 2 and r.registry_version == 5  # pulled from 'v2' / 'v5'


def test_record_run_idempotent_by_run_id(session):
    repo.record_run(session, _meta(scored=4), req_code="accountant-role")
    repo.record_run(session, _meta(scored=9), req_code="accountant-role")  # same run_id
    session.commit()
    runs = repo.list_runs(session)
    assert len(runs) == 1  # updated in place, not duplicated
    assert runs[0].counts["scored"] == 9


def test_latest_run_for_returns_newest(session):
    repo.record_run(session, _meta(run_id="accountant-role-20260709-090000"), req_code="accountant-role")
    session.commit()
    repo.record_run(session, _meta(run_id="accountant-role-20260709-120000"), req_code="accountant-role")
    session.commit()
    latest = repo.latest_run_for(session, "accountant-role")
    assert latest.run_id == "accountant-role-20260709-120000"
    assert repo.latest_run_for(session, "no-such-role") is None


def test_run_meta_from_row_reconstructs(session):
    repo.record_run(session, _meta(), req_code="accountant-role")
    session.commit()
    meta = repo.run_meta_from_row(repo.list_runs(session)[0])
    assert meta["run_id"] == "accountant-role-20260709-101500"
    assert meta["role_title"] == "Senior Accountant"
    assert meta["counts"]["scored"] == 4 and meta["bands"]["Strong"] == 2
    assert meta["versions"]["rubric"] == "accountant.rubric.v2.yaml (v2)"
    assert meta["cost_estimate"]["estimated_usd"] == 0.42


def test_run_index_item_shape(session):
    repo.record_run(session, _meta(), req_code="accountant-role")
    session.commit()
    item = repo.run_index_item(repo.list_runs(session)[0])
    assert item == {
        "req_id": "accountant-role", "role": "Senior Accountant",
        "run_id": "accountant-role-20260709-101500", "jurisdiction": "IN",
        "when": "2026-07-09T10:15:00+00:00",
        "counts": {"scored": 4, "gate_passers": 3, "errors": 0},
        "bands": {"Strong": 2, "Possible": 1, "Not a fit": 1},
        # P51: the version stamps ride along for the audit surfaces
        "versions": {"rubric": "accountant.rubric.v2.yaml (v2)",
                     "registry": "v5 (abc123def456)"},
    }


def test_run_region_scope_own_plus_global(session):
    regions_repo.upsert_region(session, "APAC", "Asia-Pacific")
    regions_repo.upsert_region(session, "AMER", "Americas")
    regions_repo.upsert_jurisdiction(session, "IN", "India", region_code="APAC")
    session.flush()
    apac = regions_repo.get_region_by_code(session, "APAC").id
    amer = regions_repo.get_region_by_code(session, "AMER").id
    repo.record_run(session, _meta(run_id="in-1", jurisdiction="IN"), req_code="in-role", region_id=apac)
    repo.record_run(session, _meta(run_id="us-1", jurisdiction="US-CA"), req_code="us-role", region_id=amer)
    repo.record_run(session, _meta(run_id="legacy-1"), req_code="legacy-role")  # region NULL
    session.commit()
    apac_ids = {r.run_id for r in repo.list_runs(session, region_id=apac)}
    assert apac_ids == {"in-1", "legacy-1"}  # own + global, never AMER's
    assert len(repo.list_runs(session)) == 3  # whole-org view


# --- service read path (run_index_backend=db) --------------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    """Point the DB helpers + service at an in-memory SQLite with the DB run index."""
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(service, "_use_run_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


def test_service_list_runs_from_db(db_backend):
    with session_mod.session_scope() as s:
        repo.record_run(s, _meta(), req_code="accountant-role")
    runs = service.list_runs()
    assert len(runs) == 1
    assert runs[0]["req_id"] == "accountant-role"
    assert runs[0]["run_id"] == "accountant-role-20260709-101500"
    # the composed one-liner the RunsAudit table shows
    assert runs[0]["meta"] == "India · 4 scored · 2 strong"


def test_service_read_run_meta_db_fallback(db_backend):
    # A req_code with no on-disk output folder → read_run_meta falls back to the row.
    with session_mod.session_scope() as s:
        repo.record_run(s, _meta(run_id="ghost-role-20260709-101500"),
                        req_code="ghost-role-no-file-xyz")
    meta = service.read_run_meta("ghost-role-no-file-xyz")
    assert meta is not None
    assert meta["run_id"] == "ghost-role-20260709-101500"
    assert meta["bands"]["Strong"] == 2


# --- importer ----------------------------------------------------------------------


def test_import_runs_scans_output(session, tmp_path):
    role_dir = tmp_path / "accountant-role"
    role_dir.mkdir()
    (role_dir / "run_meta.json").write_text(json.dumps(_meta()), encoding="utf-8")

    first = importers.import_runs(session, tmp_path)
    session.commit()
    assert first["added"] == 1 and first["skipped"] is False
    row = repo.latest_run_for(session, "accountant-role")
    assert row.req_code == "accountant-role" and row.run_id.endswith("101500")

    # Re-run: the same run_id is skipped, not duplicated.
    again = importers.import_runs(session, tmp_path)
    session.commit()
    assert again["added"] == 0 and "already indexed" in again["reason"]
    assert len(repo.list_runs(session)) == 1


def test_import_runs_no_output_dir(session, tmp_path):
    r = importers.import_runs(session, tmp_path / "nope")
    assert r["skipped"] is True and r["reason"] == "no runs on disk"


def test_import_all_includes_runs(session, tmp_path, monkeypatch):
    # import_all uses the default paths; point cwd at an empty tmp so nothing real is read.
    monkeypatch.chdir(tmp_path)
    reports = {r["domain"]: r for r in importers.import_all(session)}
    assert set(reports) == {"decisions", "audit", "review", "runs", "registry",
                            "policies", "rubrics", "requisitions", "req_contacts",
                            "preferences", "events", "hm_takes", "flags"}
    assert reports["runs"]["skipped"] is True  # no output/ here
    assert reports["registry"]["skipped"] is True  # no signals/registry.yaml here
    assert reports["policies"]["skipped"] is True  # no policies/ here
    assert reports["rubrics"]["skipped"] is True  # no jds/ here
    assert reports["requisitions"]["skipped"] is True  # no jds/ here
