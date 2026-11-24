"""Phase 7 tests — region base framework seed + read helpers + the region-aware
repo hook. Fully offline (in-memory SQLite). Enforcement/SSO are P27; here we prove
the reference data seeds idempotently, the jurisdiction→region mapping is right, the
feature catalog resolves per region, the dev user gets all-regions access, and the
service read helpers degrade gracefully.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener.api import service
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import identity as identity_repo
from resume_screener.db.repositories import regions as regions_repo
from resume_screener.db.seed import ENABLED_FEATURES, FEATURES, JURISDICTIONS, REGIONS, seed_region_framework




@pytest.fixture
def session() -> Session:
    eng = _sqlite_engine()
    sess = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield sess
    finally:
        sess.close()
        eng.dispose()


# --- seed directly -----------------------------------------------------------------


def test_seed_populates_all_reference_data(session):
    report = seed_region_framework(session, dev_user="local-user", dev_role="HRHead")
    session.commit()
    assert report["regions"] == len(REGIONS)
    assert report["jurisdictions"] == len(JURISDICTIONS)
    assert report["features"] == len(FEATURES)
    assert {r.code for r in regions_repo.list_regions(session)} == {c for c, _ in REGIONS}


def test_jurisdiction_region_mapping(session):
    seed_region_framework(session)
    session.commit()
    # the governing region derived from a jurisdiction matches the seed table
    assert regions_repo.governing_region_code(session, "IN") == "IN"
    assert regions_repo.governing_region_code(session, "SG") == "APAC"
    assert regions_repo.governing_region_code(session, "JP") == "APAC"
    assert regions_repo.governing_region_code(session, "EU") == "EMEA"
    assert regions_repo.governing_region_code(session, "US-CA") == "AMER"
    assert regions_repo.governing_region_code(session, "US-NYC") == "AMER"


def test_feature_catalog_per_region(session):
    seed_region_framework(session)
    session.commit()
    for code, _ in REGIONS:
        enabled = regions_repo.enabled_features(session, code)
        assert enabled == ENABLED_FEATURES  # the on-by-default set, §12 ones off
        assert "candidate_enrichment" not in enabled and "learning_loop" not in enabled


def test_seed_is_idempotent(session):
    seed_region_framework(session, dev_user="local-user", dev_role="HRHead")
    seed_region_framework(session, dev_user="local-user", dev_role="HRHead")  # re-run
    session.commit()
    assert len(regions_repo.list_regions(session)) == len(REGIONS)  # no duplicates
    assert len(regions_repo.list_features(session)) == len(FEATURES)
    # one grant, not two
    assert len(identity_repo.roles_for_user(session, "local-user")) == 1


def test_dev_user_gets_all_regions(session):
    seed_region_framework(session, dev_user="local-user", dev_role="HRHead")
    session.commit()
    assert identity_repo.regions_for_user(session, "local-user") is None  # None = all regions


def test_roles_in_region_hook(session):
    seed_region_framework(session)  # no dev grant this time
    identity_repo.grant_role(session, "apac.hr@example.com", "HRHead", region_code="APAC")
    identity_repo.grant_role(session, "it.admin@example.com", "IT", region_code=None)
    session.commit()
    # a regional grant applies only in its region; a global grant applies everywhere
    assert identity_repo.roles_in_region(session, "apac.hr@example.com", "APAC") == {"HRHead"}
    assert identity_repo.roles_in_region(session, "apac.hr@example.com", "EMEA") == set()
    assert identity_repo.roles_in_region(session, "apac.hr@example.com", None) == set()  # no global grant
    assert identity_repo.roles_in_region(session, "it.admin@example.com", "EMEA") == {"IT"}  # global applies
    assert identity_repo.roles_in_region(session, "it.admin@example.com", None) == {"IT"}


# --- service read helpers (what /api/regions, /api/features, /api/me consume) -------


@pytest.fixture
def db_backend(monkeypatch):
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    with session_mod.session_scope() as s:
        seed_region_framework(s, dev_user="local-user", dev_role="HRHead")
    try:
        yield
    finally:
        eng.dispose()


def test_regions_catalog(db_backend):
    catalog = {r["code"]: r for r in service.regions_catalog()}
    assert set(catalog) == {c for c, _ in REGIONS}
    assert "IN" in catalog["IN"]["jurisdictions"]
    assert set(catalog["AMER"]["jurisdictions"]) == {"US-default", "US-NYC", "US-CA"}
    assert "blind_review" in catalog["IN"]["features"]


def test_features_catalog(db_backend):
    feats = {f["key"]: f for f in service.features_catalog()}
    assert set(feats) == {k for k, _n, _d in FEATURES}
    assert feats["blind_review"]["regions"]["IN"] is True
    assert feats["learning_loop"]["regions"]["IN"] is False  # §12, off everywhere


def test_region_context_dev_user_unrestricted(db_backend):
    ctx = service.region_context("local-user")
    assert ctx["all"] is True and set(ctx["codes"]) == {c for c, _ in REGIONS}


def test_region_context_scoped_user(db_backend):
    with session_mod.session_scope() as s:
        identity_repo.grant_role(s, "apac.hr@example.com", "HRHead", region_code="APAC")
    ctx = service.region_context("apac.hr@example.com")
    assert ctx["all"] is False and ctx["codes"] == ["APAC"]


def test_read_helpers_degrade_gracefully(monkeypatch):
    # No DB configured → helpers return empty / unrestricted, never raise.
    def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(session_mod, "_maker", _boom)
    assert service.regions_catalog() == []
    assert service.features_catalog() == []
    # Unrestricted, and flagged as NOT an ungranted account — a DB outage must not be
    # reported as "this person has no regions assigned" (2026-08-27).
    assert service.region_context("local-user") == {
        "all": True, "codes": [], "ungranted": False}
