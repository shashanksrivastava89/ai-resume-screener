"""Phase 6c tests — versioned rubrics as config-as-data in the DB.

Fully offline (in-memory SQLite). Three layers:
1. the rubric repo directly (save/latest round-trip, weight normalization, ordering,
   versioning, req_code key, NULL requisition_id until Phase 6d);
2. rubric.py with rubric_backend=db — the two-step latest_rubric_path→load_rubric API
   and next_version/save_rubric switch, plus the explicit-rubric-file override;
3. the importer backfilling jds/<stem>.rubric.vN.yaml (per-table guard, all versions).
"""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import rubric as rubric_mod
from resume_screener.db import engine as engine_mod
from resume_screener.db import importers
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import rubric as repo
from resume_screener.models import Requirement, Rubric


def _rubric(version=1, role_title="Senior Accountant") -> Rubric:
    return Rubric(
        role_title=role_title, jd_ref="jds/acc.md", version=version,
        requirements=[
            Requirement(id="r01", text="Treasury experience", type="experience",
                        kind="must_have", weight=3),
            Requirement(id="r02", text="Ind AS knowledge", type="skill",
                        kind="nice_to_have", weight=1),
            Requirement(id="r03", text="CA qualification", type="certification",
                        kind="hard_gate", weight=0),
        ],
    )




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


def test_save_and_latest_round_trips(session):
    repo.save_rubric(session, "acc", _rubric(1), version=1)
    session.commit()
    rub = repo.latest_rubric(session, "acc")
    assert rub.role_title == "Senior Accountant" and rub.version == 1
    assert [r.id for r in rub.requirements] == ["r01", "r02", "r03"]  # ordinal order preserved
    assert [r.kind for r in rub.requirements] == ["must_have", "nice_to_have", "hard_gate"]
    # non-gate weights (3,1) renormalize to sum 1 on load; the gate stays 0
    assert rub.requirements[0].weight == 0.75 and rub.requirements[1].weight == 0.25
    assert rub.requirements[2].weight == 0.0
    assert abs(sum(r.weight for r in rub.requirements) - 1.0) < 1e-6


def test_requisition_id_null_until_populated(session):
    repo.save_rubric(session, "acc", _rubric(1), version=1)
    session.commit()
    row = repo._latest_row(session, "acc")
    assert row.req_code == "acc" and row.requisition_id is None


def test_versions_accumulate_latest_wins(session):
    repo.save_rubric(session, "acc", _rubric(1, "Accountant"), version=1)
    repo.save_rubric(session, "acc", _rubric(2, "Senior Accountant"), version=2)
    session.commit()
    assert repo.latest_version(session, "acc") == 2
    assert repo.latest_rubric(session, "acc").role_title == "Senior Accountant"
    assert repo.has_any(session) is True
    assert repo.latest_rubric(session, "nope") is None
    assert repo.latest_version(session, "nope") == 0


# --- rubric.py switch (rubric_backend=db) ------------------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(rubric_mod, "_use_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


def test_save_load_cycle_db(db_backend):
    assert rubric_mod.next_version("jds/acc.md") == 1
    assert rubric_mod.latest_rubric_path("jds/acc.md") is None  # none yet
    handle = rubric_mod.save_rubric(_rubric(), "jds/acc.md")
    assert str(handle) == str(Path("jds/acc.md"))  # JD path is the handle
    # the two-step API the callers use: latest→load
    path = rubric_mod.latest_rubric_path("jds/acc.md")
    assert path is not None
    rub = rubric_mod.load_rubric(path)
    assert rub.version == 1
    assert Path(rub.jd_ref) == Path("jds/acc.md")  # jd_ref reconstructed (OS-native sep)
    assert rub.role_title == "Senior Accountant"
    assert rubric_mod.next_version("jds/acc.md") == 2  # increments off the DB


def test_second_save_bumps_version_db(db_backend):
    rubric_mod.save_rubric(_rubric(role_title="Accountant"), "jds/acc.md")
    rubric_mod.save_rubric(_rubric(role_title="Senior Accountant"), "jds/acc.md")
    rub = rubric_mod.load_rubric("jds/acc.md")
    assert rub.version == 2 and rub.role_title == "Senior Accountant"


def test_explicit_rubric_file_still_reads_file_db(db_backend, tmp_path):
    # An explicit *.rubric.vN.yaml path is read from disk even under the DB backend
    # (the `--rubric` override), never routed to the DB.
    f = tmp_path / "acc.rubric.v1.yaml"
    f.write_text(yaml.safe_dump(_rubric(1).model_dump(), allow_unicode=True), encoding="utf-8")
    rub = rubric_mod.load_rubric(f)
    assert rub.version == 1 and rub.role_title == "Senior Accountant"


# --- importer ----------------------------------------------------------------------


def test_import_rubrics_all_versions(session, tmp_path):
    jds = tmp_path / "jds"
    jds.mkdir()
    (jds / "acc.md").write_text("# Senior Accountant\nJD body", encoding="utf-8")
    (jds / "acc.rubric.v1.yaml").write_text(
        yaml.safe_dump(_rubric(1, "Accountant").model_dump(), allow_unicode=True), encoding="utf-8")
    (jds / "acc.rubric.v2.yaml").write_text(
        yaml.safe_dump(_rubric(2, "Senior Accountant").model_dump(), allow_unicode=True), encoding="utf-8")

    report = importers.import_rubrics(session, jds)
    session.commit()
    assert report["added"] == 2 and report["skipped"] is False
    assert repo.latest_version(session, "acc") == 2
    assert repo.latest_rubric(session, "acc").role_title == "Senior Accountant"

    again = importers.import_rubrics(session, jds)
    assert again["skipped"] is True and "already has rows" in again["reason"]


def test_import_rubrics_no_dir(session, tmp_path):
    r = importers.import_rubrics(session, tmp_path / "nope")
    assert r["skipped"] is True and r["reason"] == "no dir"
