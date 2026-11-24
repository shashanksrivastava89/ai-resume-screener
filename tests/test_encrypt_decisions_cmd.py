"""The 2026-10-14 decision migration and the one-off seal sweep. Offline.

`c8e2f4a6b1d3` adds `decision.req_code` (backfilled from the run it was made on)
and `decision.verdict_enc`; `screener db encrypt-decisions` then seals every plain
row. Both run against a real Alembic upgrade on a temporary SQLite file, so the
migration is exercised as written, not as the ORM imagines it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from typer.testing import CliRunner

from resume_screener import fieldcrypto
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "b9c4e7f2a3d1"


def _forget_engines():
    engine_mod.reset_engine()
    session_mod._makers.clear()


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """A temp SQLite at the revision BEFORE this change, plus an alembic Config
    (no ini file, so alembic never reconfigures the test session's logging)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    _forget_engines()
    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, BEFORE)
    yield cfg, engine_mod.get_engine()
    _forget_engines()


def _insert_old_rows(engine):
    now = "2026-10-01 10:00:00"
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO run (id, run_id, req_code, created_at, updated_at) "
            "VALUES ('r1', 'run-1', 'finance-analyst', :now, :now)"), {"now": now})
        for cid, run_id in (("c1", "run-1"), ("c2", "run-orphan")):
            conn.execute(sa.text(
                "INSERT INTO decision (run_id, candidate_id, reviewer, suggestion, "
                "human_action, final_band, reason, ts, created_at, updated_at) VALUES "
                "(:run, :cid, 'ta@example.com', 'Strong (91)', 'agree', 'Strong', "
                "'Good treasury depth', '2026-10-01T10:00:00+00:00', :now, :now)"),
                {"run": run_id, "cid": cid, "now": now})


def test_upgrade_backfills_the_requisition_from_the_run(migrated_db):
    cfg, engine = migrated_db
    _insert_old_rows(engine)
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = dict(conn.execute(sa.text(
            "SELECT candidate_id, req_code FROM decision ORDER BY candidate_id")).all())
        enc = conn.execute(sa.text("SELECT verdict_enc FROM decision")).scalars().all()
    # a decision whose run is indexed learns its requisition; one whose run is not
    # stays NULL — it simply never carries forward, exactly as before
    assert rows == {"c1": "finance-analyst", "c2": None}
    assert enc == [None, None]  # nothing is sealed by the migration itself


def test_downgrade_removes_both_columns(migrated_db):
    cfg, engine = migrated_db
    command.upgrade(cfg, "head")
    command.downgrade(cfg, BEFORE)
    cols = {c["name"] for c in sa.inspect(engine_mod.get_engine()).get_columns("decision")}
    assert "req_code" not in cols and "verdict_enc" not in cols


def test_encrypt_decisions_seals_once_and_says_how_many(migrated_db, monkeypatch):
    from resume_screener.cli import app as cli_app
    from resume_screener.db.repositories import decisions as repo
    from resume_screener.db.session import session_scope

    cfg, engine = migrated_db
    _insert_old_rows(engine)
    command.upgrade(cfg, "head")
    runner = CliRunner()

    monkeypatch.delenv(fieldcrypto.ENV_SECRET, raising=False)
    refused = runner.invoke(cli_app, ["db", "encrypt-decisions"])
    assert refused.exit_code == 1 and "no key to seal with" in refused.output

    key = os.urandom(32)
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    fieldcrypto.set_key_provider(lambda version: ("k1", key))
    first = runner.invoke(cli_app, ["db", "encrypt-decisions"])
    assert first.exit_code == 0, first.output
    assert "Encrypted 2 decision row(s) · 0 were already encrypted" in first.output
    again = runner.invoke(cli_app, ["db", "encrypt-decisions"])
    assert "Encrypted 0 decision row(s) · 2 were already encrypted" in again.output
    rewrap = runner.invoke(cli_app, ["db", "encrypt-decisions", "--rewrap"])
    assert rewrap.exit_code == 0, rewrap.output
    assert "re-sealed 0 under the current key · 2 already current" in rewrap.output

    with engine.connect() as conn:
        plain = conn.execute(sa.text(
            "SELECT suggestion, reason FROM decision")).all()
    assert plain == [(None, ""), (None, "")]
    assert "Good treasury depth" not in first.output + again.output  # counts, never values
    with session_scope() as s:
        assert [d.reason for d in repo.list_decisions(s)] == ["Good treasury depth"] * 2
        assert [d.req_id for d in repo.list_decisions(s)] == ["finance-analyst", ""]


def test_head_carries_the_checklist_flag_columns(migrated_db):
    """d7f3a9b2c5e8 (2026-10-16): the requirement table can hold the critic's flag."""
    cfg, engine = migrated_db
    command.upgrade(cfg, "head")
    cols = {c["name"] for c in sa.inspect(engine_mod.get_engine()).get_columns("requirement")}
    assert {"critic_level", "critic_reason"} <= cols
    command.downgrade(cfg, "c8e2f4a6b1d3")
    cols = {c["name"] for c in sa.inspect(engine_mod.get_engine()).get_columns("requirement")}
    assert "critic_level" not in cols and "critic_reason" not in cols
