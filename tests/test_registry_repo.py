"""Signal-registry rows in the DB after the 2026-07-18 merge.

The governed scoring model (signals/scoring_model.vN.yaml) is the registry's
source of truth; the DB rows remain as an import target (forward-compatible
with a future config-as-data move) — the old registry_backend read/publish
routing is retired with signals/registry.yaml. Fully offline (in-memory
SQLite): the importer sources from the model, the repo round-trips the §4
SignalRegistry shape, floor topics arrive locked, and the import guard makes
`screener db import` re-runnable."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener.db import importers
from resume_screener.db.repositories import registry as repo
from resume_screener.scoring_model import load_scoring_model

EXPECTED_SIGNAL_IDS = {
    "tenure_pattern_vs_stability", "preferred_domain_background",
    "regulated_environment_exposure", "skill_recency", "scope_trajectory",
    "hands_on_currency", "domain_transfer_distance", "certification_currency",
    "language_evidence", "quota_attainment_evidence", "deal_profile_fit",
    "public_work_relevance",
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


def test_importer_sources_from_the_scoring_model(session):
    out = importers.import_registry(session)
    session.commit()
    assert out["domain"] == "registry" and not out["skipped"]
    model = repo.load_registry_model(session)
    assert {s.id for s in model.signals} == EXPECTED_SIGNAL_IDS
    # the topics of the model the importer read (the latest published version)
    assert {e.id for e in model.excluded} == {t.id for t in load_scoring_model().topics}


def test_import_is_rerunnable(session):
    importers.import_registry(session)
    session.commit()
    again = importers.import_registry(session)
    assert again["skipped"] or again["added"] == 0


def test_round_trip_matches_the_model_registry(session):
    importers.import_registry(session)
    session.commit()
    from resume_screener.registry import load_registry

    file_view = load_registry("signals")
    db_view = repo.load_registry_model(session)
    assert db_view.version == file_view.version
    assert {s.id: s.cap_points for s in db_view.signals} == \
        {s.id: s.cap_points for s in file_view.signals}
