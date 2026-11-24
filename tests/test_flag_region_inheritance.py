"""A region that never spoke about a flag must inherit the flag's default.

The hosted regression this pins: multi-résumé (iCIMS Bulk Print) splitting silently
stopped. The flag's declared default is ON and no region had ever overridden it —
but the per-region matrix was built from a SET of enabled keys, so "no override" and
"overridden to off" were indistinguishable, and every region read every un-seeded
flag as OFF. `_run_split` then did nothing and bulk files landed unsplit.

The inheritance rule is stated identically in three places and they must agree:
feature_flags.flag_enabled, the admin page (`code in f.regions ? … : f.default`),
and the catalog these two read.

Offline: in-memory SQLite + a tmp features.yaml.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine

from resume_screener import feature_flags
from resume_screener.api import service
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import flags as flags_repo
from resume_screener.db.repositories import regions as regions_repo

CATALOG = """\
version: 1
features:
  multi_resume_split:
    name: "Split multi-résumé files"
    description: "Split an iCIMS Bulk Print into individual résumés."
    default: true
  learning_loop:
    name: "Governed learning loop"
    description: "Deferred to §12."
    default: false
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A seeded region framework + a two-flag catalog, wired to a throwaway engine."""
    engine = make_test_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: engine)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(feature_flags, "_use_flags_db", lambda: True)

    (tmp_path / "signals").mkdir()
    (tmp_path / "signals" / "features.yaml").write_text(CATALOG, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with session_mod.session_scope() as session:
        regions_repo.upsert_region(session, "IN", "India")
        regions_repo.upsert_region(session, "AMER", "Americas")
        # Both flags exist in the DB catalog with their declared defaults, and NO
        # region has an opinion about either — exactly what `db import` leaves behind
        # for a flag the region seed never listed.
        flags_repo.upsert_flag(session, "multi_resume_split",
                               name="Split multi-résumé files", default_enabled=True)
        flags_repo.upsert_flag(session, "learning_loop",
                               name="Governed learning loop", default_enabled=False)
    try:
        yield
    finally:
        engine.dispose()


def test_a_flag_no_region_overrode_keeps_its_default(db):
    """The regression: default ON must stay ON for a region with no override."""
    assert feature_flags.flag_enabled("multi_resume_split", "IN") is True
    assert feature_flags.flag_enabled("multi_resume_split", "AMER") is True
    assert feature_flags.flag_enabled("learning_loop", "IN") is False


def test_the_catalog_reports_only_real_overrides(db):
    entry = next(e for e in feature_flags.catalog() if e["key"] == "multi_resume_split")
    assert entry["default"] is True
    assert entry["regions"] == {}  # nobody overrode it — not "false everywhere"


def test_an_explicit_override_still_wins_both_ways(db):
    with session_mod.session_scope() as session:
        regions_repo.set_region_feature(session, "IN", "multi_resume_split", False)
        regions_repo.set_region_feature(session, "AMER", "learning_loop", True)

    assert feature_flags.flag_enabled("multi_resume_split", "IN") is False   # off despite default
    assert feature_flags.flag_enabled("multi_resume_split", "AMER") is True  # still inherits
    assert feature_flags.flag_enabled("learning_loop", "AMER") is True       # on despite default
    assert feature_flags.flag_enabled("learning_loop", "IN") is False

    entry = next(e for e in feature_flags.catalog() if e["key"] == "multi_resume_split")
    assert entry["regions"] == {"IN": False}  # the one region that actually spoke


def test_feature_overrides_and_enabled_features_agree(db):
    with session_mod.session_scope() as session:
        regions_repo.set_region_feature(session, "IN", "multi_resume_split", False)
        regions_repo.set_region_feature(session, "IN", "learning_loop", True)

        overrides = regions_repo.feature_overrides(session, "IN")
        assert overrides == {"multi_resume_split": False, "learning_loop": True}
        # the older set view keeps its meaning: the keys explicitly turned ON
        assert regions_repo.enabled_features(session, "IN") == {"learning_loop"}
        assert regions_repo.feature_overrides(session, "AMER") == {}
        assert regions_repo.feature_overrides(session, "NOPE") == {}


def test_service_catalog_omits_regions_that_never_spoke(db):
    with session_mod.session_scope() as session:
        regions_repo.set_region_feature(session, "AMER", "multi_resume_split", False)

    catalog = {f["key"]: f for f in service.features_catalog()}
    assert catalog["multi_resume_split"]["regions"] == {"AMER": False}
    assert catalog["learning_loop"]["regions"] == {}
