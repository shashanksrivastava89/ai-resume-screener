"""Phase 6d tests — JD / requisition identity in the DB + the FK backfill.

Fully offline (in-memory SQLite). Covers the repo (upsert + contract), the FK
backfill that links the soft req_code references (rubric/run/screening_job) to real
requisitions, the ATS fetch_requisitions switch, and the importer over jds/.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener.ats import local_folder
from resume_screener.db import engine as engine_mod
from resume_screener.db import importers
from resume_screener.db import session as session_mod
from resume_screener.db.models.requisition import JdDocument
from resume_screener.db.models.rubric import Rubric as RubricRow
from resume_screener.db.models.runtime import Run as RunRow
from resume_screener.db.models.runtime import ScreeningJob as ScreeningJobRow
from resume_screener.db.repositories import requisitions as repo




@pytest.fixture
def session() -> Session:
    eng = _sqlite_engine()
    sess = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield sess
    finally:
        sess.close()
        eng.dispose()


def _seed_one(session, req_code="acc", title="Senior Accountant", location="IN"):
    doc = repo.upsert_jd_document(session, content_hash=f"hash-{req_code}", title=title,
                                  source_ref=f"jds/{req_code}.md", media_type="md", text="JD body")
    return repo.upsert_requisition(session, req_code=req_code, role_title=title,
                                   job_location=location, jd_document_id=doc.id, slug=req_code)


# --- repo directly -----------------------------------------------------------------


def test_upsert_and_contract(session):
    _seed_one(session)
    session.commit()
    reqs = repo.list_requisitions(session)
    assert len(reqs) == 1
    r = reqs[0]
    assert r.req_id == "acc" and r.role_title == "Senior Accountant"
    assert r.jd_ref == "jds/acc.md" and r.job_location == "IN"
    assert repo.get_requisition(session, "acc").req_id == "acc"
    assert repo.get_requisition(session, "nope") is None
    assert repo.has_any(session) is True


def test_upsert_is_idempotent(session):
    _seed_one(session, title="Accountant")
    _seed_one(session, title="Senior Accountant")  # same req_code → update in place
    session.commit()
    reqs = repo.list_requisitions(session)
    assert len(reqs) == 1 and reqs[0].role_title == "Senior Accountant"


def test_jd_dedup_by_content_hash(session):
    d1 = repo.upsert_jd_document(session, content_hash="same", title="A", source_ref="a.md",
                                 media_type="md", text="x")
    d2 = repo.upsert_jd_document(session, content_hash="same", title="B", source_ref="b.md",
                                 media_type="md", text="y")
    session.commit()
    assert d1.id == d2.id  # one document identity for identical content


# --- create / delete write-through (the API's identity writes) ----------------------


def test_sync_new_role_writes_the_pair_of_identity_rows(session, tmp_path):
    jd = tmp_path / "finance-analyst.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury accounting.\n", encoding="utf-8")

    created = repo.sync_new_role(session, req_code="finance-analyst",
                                 role_title="Finance Analyst", job_location="IN",
                                 jd_path=jd, slug="finance-analyst",
                                 created_by="ta@example.com")
    session.commit()

    assert created.req_id == "finance-analyst" and created.jd_ref == str(jd)
    listed = repo.list_requisitions(session)
    assert [r.req_id for r in listed] == ["finance-analyst"]
    assert listed[0].job_location == "IN"
    assert repo.get_requisition_row(session, "finance-analyst").created_by == "ta@example.com"


def test_sync_new_role_and_the_importer_agree_on_one_row(session, tmp_path):
    """The create write-through hashes the JD exactly as `db import` does, so a
    later import updates these rows instead of duplicating the JD document."""
    jds = tmp_path / "jds"
    jds.mkdir()
    jd = jds / "finance-analyst.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury accounting.\n", encoding="utf-8")
    repo.sync_new_role(session, req_code="finance-analyst", role_title="Finance Analyst",
                       job_location="IN", jd_path=jd, slug="finance-analyst")
    session.commit()

    importers.import_requisitions(session, jds, "IN")
    session.commit()

    assert len(repo.list_requisitions(session)) == 1
    assert session.query(JdDocument).count() == 1


def test_delete_requisition_removes_the_row_and_its_checklists(session, tmp_path):
    jd = tmp_path / "finance-analyst.md"
    jd.write_text("# Finance Analyst\n\nbody\n", encoding="utf-8")
    repo.sync_new_role(session, req_code="finance-analyst", role_title="Finance Analyst",
                       job_location="IN", jd_path=jd)
    # A checklist saved BEFORE the requisition existed carries a NULL FK, so no DB
    # cascade would reach it — a recreated req_id would otherwise inherit it.
    session.add(RubricRow(req_code="finance-analyst", version=1, role_title="Finance Analyst"))
    session.commit()

    assert repo.delete_requisition(session, "finance-analyst") is True
    session.commit()

    assert repo.list_requisitions(session) == []
    assert session.query(RubricRow).filter_by(req_code="finance-analyst").count() == 0
    assert session.query(JdDocument).count() == 0  # nothing else referenced it
    assert repo.delete_requisition(session, "finance-analyst") is False  # already gone


def test_delete_keeps_a_jd_document_another_role_still_uses(session, tmp_path):
    jd = tmp_path / "a.md"
    jd.write_text("# Analyst\n\nsame body\n", encoding="utf-8")
    repo.sync_new_role(session, req_code="a", role_title="Analyst", job_location="IN",
                       jd_path=jd)
    twin = tmp_path / "b.md"
    twin.write_text(jd.read_text(encoding="utf-8"), encoding="utf-8")  # identical -> one doc
    repo.sync_new_role(session, req_code="b", role_title="Analyst", job_location="IN",
                       jd_path=twin)
    session.commit()

    repo.delete_requisition(session, "a")
    session.commit()

    assert [r.req_id for r in repo.list_requisitions(session)] == ["b"]
    assert session.query(JdDocument).count() == 1


# --- FK backfill -------------------------------------------------------------------


def test_backfill_links_soft_references(session):
    # rows that carry only the soft req_code link (rubrics 6c, runs 4, jobs 5)
    rubric = RubricRow(req_code="acc", version=1, role_title="Senior Accountant")
    run = RunRow(run_id="acc-20260710-0900", req_code="acc")
    job = ScreeningJobRow(id="job1", req_code="acc", status="done")
    orphan = RunRow(run_id="orphan-run", req_code="no-such-role")  # no requisition → stays NULL
    session.add_all([rubric, run, job, orphan])
    session.flush()
    req = _seed_one(session, req_code="acc")
    session.commit()

    linked = repo.backfill_requisition_links(session)
    session.commit()
    assert linked == 3  # rubric + run + job for 'acc'
    assert rubric.requisition_id == req.id
    assert run.requisition_id == req.id
    assert job.requisition_id == req.id
    assert orphan.requisition_id is None  # unmatched req_code stays NULL
    # idempotent: a second pass links nothing new
    assert repo.backfill_requisition_links(session) == 0


# --- ATS fetch_requisitions switch -------------------------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(local_folder, "_use_requisitions_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


def test_ats_fetch_requisitions_reads_db(db_backend):
    with session_mod.session_scope() as s:
        _seed_one(s, req_code="acc", title="Senior Accountant")
        _seed_one(s, req_code="sales", title="Sales Manager", location="US-CA")
    ats = local_folder.LocalFolderATS(jd_dir="jds", resumes_dir="data/resumes",
                                      default_job_location="IN")
    reqs = {r.req_id: r for r in ats.fetch_requisitions()}
    assert set(reqs) == {"acc", "sales"}
    assert reqs["sales"].role_title == "Sales Manager" and reqs["sales"].job_location == "US-CA"


# --- importer ----------------------------------------------------------------------


def test_import_requisitions_from_jds(session, tmp_path):
    jds = tmp_path / "jds"
    jds.mkdir()
    (jds / "acc.md").write_text("# Senior Accountant\nbody", encoding="utf-8")
    (jds / "acc.req.json").write_text('{"schema": 1, "job_location": "IN"}', encoding="utf-8")
    (jds / "sales.md").write_text("# Sales Manager\nbody", encoding="utf-8")
    # a rubric row that should get its FK backfilled by the import
    session.add(RubricRow(req_code="acc", version=1, role_title="Senior Accountant"))
    session.flush()

    report = importers.import_requisitions(session, jds, "IN")
    session.commit()
    assert report["added"] == 2 and report["skipped"] is False
    assert "backfilled" in report["reason"]  # the acc rubric got linked
    assert {r.req_id for r in repo.list_requisitions(session)} == {"acc", "sales"}

    # re-run: creation guarded, backfill still runs (idempotent)
    again = importers.import_requisitions(session, jds, "IN")
    assert again["skipped"] is True and "already has rows" in again["reason"]


def test_import_requisitions_no_dir(session, tmp_path):
    r = importers.import_requisitions(session, tmp_path / "nope", "IN")
    assert r["skipped"] is True and r["reason"] == "no dir"
