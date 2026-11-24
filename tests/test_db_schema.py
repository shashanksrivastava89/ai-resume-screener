"""Phase 1 schema tests — fully offline (in-memory SQLite, no Postgres, no Azure).

Validates that the whole model creates cleanly, the region base-framework repos
round-trip, and the key integrity rules hold (natural-key uniqueness, the
all-regions vs scoped grant, owned-child cascade, and the JD<>résumé uniqueness).
The same models run on Postgres in prod; these tests keep the CI loop fast.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from resume_screener.db.base import Base
from resume_screener.db import models as m
from resume_screener.db.repositories import identity, regions


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # SQLite enforces FKs only when asked
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def test_all_tables_created(session):
    # 28 tables: the P33 hard_filter drop (single-list rubric), then P49 dropped the
    # two speculative hr_preference_set/_override tables and added five v2-domain
    # tables (hr_preference, app_event, hm_take, checklist_approval, legal_hold).
    assert len(Base.metadata.tables) == 28
    assert "hard_filter" not in Base.metadata.tables
    assert "hr_preference_set" not in Base.metadata.tables
    assert "hr_preference_override" not in Base.metadata.tables
    for name in ("region", "jurisdiction", "feature", "region_feature", "app_user",
                 "user_region_role", "jd_document", "requisition", "requisition_contact",
                 "rubric", "requirement", "candidate", "requisition_candidate",
                 "hr_preference", "app_event", "hm_take", "checklist_approval", "legal_hold",
                 "signal", "policy_pack", "run", "decision",
                 "screening_job", "audit_entry", "review_signoff"):
        assert name in Base.metadata.tables


def test_region_feature_catalog_roundtrip(session):
    regions.upsert_region(session, "IN", "India")
    regions.upsert_region(session, "US", "United States")
    regions.upsert_jurisdiction(session, "US-CA", "California", law="FEHA / CCPA", region_code="US")
    regions.upsert_feature(session, "blind_review", "Blind review")
    regions.upsert_feature(session, "hm_portal", "Hiring-manager portal")
    regions.set_region_feature(session, "IN", "blind_review", True)
    regions.set_region_feature(session, "IN", "hm_portal", False)
    session.commit()

    assert {r.code for r in regions.list_regions(session)} == {"IN", "US"}
    assert [j.code for j in regions.list_jurisdictions(session, region_code="US")] == ["US-CA"]
    # Feature catalog: only the enabled feature comes back for the region.
    assert regions.enabled_features(session, "IN") == {"blind_review"}


def test_upserts_are_idempotent(session):
    regions.upsert_region(session, "IN", "India")
    regions.upsert_region(session, "IN", "India (updated)")  # same code -> update, not insert
    session.commit()
    rows = regions.list_regions(session)
    assert len(rows) == 1 and rows[0].name == "India (updated)"


def test_region_code_unique(session):
    session.add_all([m.Region(code="DUP", name="One"), m.Region(code="DUP", name="Two")])
    with pytest.raises(IntegrityError):
        session.flush()


def test_all_regions_grant_vs_scoped(session):
    regions.upsert_region(session, "IN", "India")
    regions.upsert_region(session, "US", "United States")
    # IT admin: all-regions grant (region_code=None) -> None means "everything".
    identity.grant_role(session, "it@example.com", "IT", region_code=None)
    # Regional HR Head: scoped to IN only.
    identity.grant_role(session, "hr.in@example.com", "HRHead", region_code="IN")
    session.commit()

    assert identity.regions_for_user(session, "it@example.com") is None
    assert identity.regions_for_user(session, "hr.in@example.com") == {"IN"}
    assert identity.regions_for_user(session, "nobody@example.com") == set()


def test_grant_role_is_idempotent(session):
    regions.upsert_region(session, "IN", "India")
    identity.grant_role(session, "hr.in@example.com", "HRHead", region_code="IN")
    identity.grant_role(session, "hr.in@example.com", "HRHead", region_code="IN")
    session.commit()
    assert len(identity.roles_for_user(session, "hr.in@example.com")) == 1


def test_rubric_owns_requirements_cascade(session):
    req = m.Requisition(req_code="REQ-1", role_title="Accountant")
    session.add(req)
    session.flush()
    rubric = m.Rubric(requisition_id=req.id, version=1, role_title="Accountant")
    rubric.requirements = [
        m.Requirement(handle="r01", text="GST filing", kind="must_have", weight=3),
        m.Requirement(handle="r02", text="Ind AS", kind="nice_to_have", weight=1),
    ]
    session.add(rubric)
    session.commit()
    assert session.query(m.Requirement).count() == 2

    session.delete(rubric)  # deleting the parent removes its owned children
    session.commit()
    assert session.query(m.Requirement).count() == 0


def test_cross_region_hiring_governing_vs_origin(session):
    # Region owns the jurisdiction's rules/audit; the creator sits elsewhere.
    regions.upsert_region(session, "APAC", "Asia-Pacific")
    regions.upsert_region(session, "AMER", "Americas")
    regions.upsert_jurisdiction(session, "IN", "India", region_code="APAC")
    regions.upsert_jurisdiction(session, "US-CA", "California", region_code="AMER")
    amer = regions.get_region_by_code(session, "AMER")

    # A US-CA (Americas) recruiter opens a role located in India.
    req = m.Requisition(
        req_code="REQ-XR", role_title="Financial Analyst",
        job_location="IN", origin_region_id=amer.id,
    )
    session.add(req)
    session.commit()

    # Governing region follows the JOB's jurisdiction (India -> APAC), not the creator.
    assert regions.governing_region_code(session, req.job_location) == "APAC"
    # Origin region records who is running it (Americas) — decoupled from the law.
    assert req.origin_region_id == amer.id
    # Sanity: the reverse direction resolves the other way.
    assert regions.governing_region_code(session, "US-CA") == "AMER"


def test_governing_region_unknown_jurisdiction_is_none(session):
    regions.upsert_jurisdiction(session, "IN", "India")  # not assigned to a region
    assert regions.governing_region_code(session, "IN") is None
    assert regions.governing_region_code(session, "ZZ") is None


def test_requisition_candidate_mapping_unique(session):
    req = m.Requisition(req_code="REQ-2", role_title="Sales Manager")
    cand = m.Candidate(candidate_id="abc123def456", label="jane_doe")
    session.add_all([req, cand])
    session.flush()
    session.add(m.RequisitionCandidate(requisition_id=req.id, candidate_id=cand.candidate_id))
    session.flush()
    # Same résumé attached to the same requisition twice -> rejected.
    session.add(m.RequisitionCandidate(requisition_id=req.id, candidate_id=cand.candidate_id))
    with pytest.raises(IntegrityError):
        session.flush()
