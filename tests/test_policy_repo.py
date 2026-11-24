"""Phase 6b tests — jurisdiction policy packs as config-as-data in the DB.

Fully offline (in-memory SQLite). Proves the file→DB switch preserves the two
load-bearing guarantees — the base⊕overlay MERGE and FLOOR PROTECTION — plus the
US-default fallback, and that publish_pack on rows keeps the version-bump + floor
rail. The file-backed test_policy.py / test_us_packs.py still cover the file path.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import governance, policy as policy_mod
from resume_screener.db import engine as engine_mod
from resume_screener.db import importers
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import policy as repo
from resume_screener.policy import FLOOR_ITEMS, PolicyError, resolve_policy
from resume_screener.registry import apply_policy, load_registry
from tests.packdata import pack_days

ALL_PACKS = ["IN", "SG", "JP", "EU", "US-default", "US-NYC", "US-CA"]




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


def test_import_then_load_round_trips(session):
    report = importers.import_policies(session)
    session.commit()
    assert report["skipped"] is False and report["added"] >= len(ALL_PACKS) + 1  # + base
    assert set(repo.list_pack_jurisdictions(session)) >= set(ALL_PACKS)
    assert "base" not in repo.list_pack_jurisdictions(session)  # base excluded
    inp = repo.load_pack_model(session, "IN")
    assert inp.jurisdiction == "IN"
    assert inp.signal_overrides["language_evidence"].cap_points == 3
    assert inp.signal_overrides["tenure_pattern_vs_stability"].params["short_stint_months"] == 9
    base = repo.load_pack_model(session, "base")
    assert all(base.floor[i] is True for i in FLOOR_ITEMS)


def test_import_is_guarded(session):
    importers.import_policies(session)
    session.commit()
    again = importers.import_policies(session)
    assert again["skipped"] is True and "already has rows" in again["reason"]


def test_save_pack_version_bumps_and_flags_current(session):
    importers.import_policies(session)
    session.commit()
    before = repo.current_pack_version(session, "IN")
    repo.save_pack_version(session, "IN",
                           {"keep_at_least_days": 900, "purge_by_days": 90, "signal_overrides": {}},
                           version=before + 1, published_by="t")
    session.commit()
    assert repo.current_pack_version(session, "IN") == before + 1
    saved = repo.load_pack_model(session, "IN")
    assert (saved.keep_at_least_days, saved.purge_by_days) == (900, 90)


# --- resolve_policy over the DB (policy_backend=db) --------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    """In-memory SQLite seeded with the packs; policy + governance routed to it."""
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(policy_mod, "_use_db", lambda: True)
    monkeypatch.setattr(governance, "_use_policy_db", lambda: True)
    with session_mod.session_scope() as s:
        importers.import_policies(s)
    try:
        yield
    finally:
        eng.dispose()


def test_every_pack_resolves_with_full_floor_db(db_backend):
    for code in ALL_PACKS:
        pol = resolve_policy(code)
        assert pol.pack == code
        for item in FLOOR_ITEMS:
            assert pol.floor.get(item) is True, f"{code} lost floor item {item}"


def test_in_merge_matches_file_semantics_db(db_backend):
    pol = resolve_policy("IN")
    tenure = pol.signal_overrides["tenure_pattern_vs_stability"].params
    assert tenure["short_stint_months"] == 9  # overlay wins
    assert tenure["min_stints_for_pattern"] == 2  # base key survives the deep merge
    assert pol.signal_overrides["language_evidence"].cap_points == 3
    assert any("human reviewer" in o.lower() for o in pol.obligations)  # base
    assert any("dpdp" in o.lower() for o in pol.obligations)  # overlay
    assert (pol.keep_at_least_days, pol.purge_by_days) == pack_days("IN")


def test_us_state_falls_back_to_us_default_db(db_backend):
    pol = resolve_policy("US-TX")
    assert pol.jurisdiction == "US-TX" and pol.pack == "US-default"
    assert any("US-default" in n for n in pol.notes)


def test_unknown_jurisdiction_is_error_db(db_backend):
    with pytest.raises(PolicyError, match="No policy pack"):
        resolve_policy("ZZ")
    with pytest.raises(PolicyError):
        resolve_policy("base")


def test_in_vs_eu_enabled_signals_differ_db(db_backend):
    registry = load_registry("signals/registry.yaml")  # registry stays file-backed here
    enabled_in = {s.id for s in apply_policy(registry, resolve_policy("IN"))[0]}
    enabled_eu = {s.id for s in apply_policy(registry, resolve_policy("EU"))[0]}
    assert "language_evidence" in enabled_in
    assert "language_evidence" not in enabled_eu  # EU disables it via a signal override


# --- publish_pack on DB rows (floor rail + version bump identical to file) ----------


def test_publish_pack_bumps_version_and_audits_db(db_backend, tmp_path):
    log = tmp_path / "audit.jsonl"
    v0 = _pack_version("IN")
    version = governance.publish_pack("IN", purge_by_days=200, author="tester",
                                      rationale="calibrate retention", log_path=log)
    assert version == v0 + 1
    assert resolve_policy("IN").purge_by_days == 200  # re-resolves from the new row
    audit = governance.load_audit(log)
    assert audit[-1]["action"] == "publish_pack" and audit[-1]["version"] == version
    assert "IN" in audit[-1]["summary"] and "purge" in audit[-1]["summary"]


def test_publish_pack_nothing_changed_db(db_backend, tmp_path):
    with pytest.raises(PolicyError, match="Nothing changed"):
        governance.publish_pack("IN", author="t", rationale="noop", log_path=tmp_path / "a.jsonl")


def test_publish_pack_floor_rail_rejects_disable():
    # publish_pack has no editable 'floor' field, so the floor rail is the guard that
    # a pack about to be saved can never disable one — it holds on the DB path too.
    from resume_screener.models import PolicyPack

    with pytest.raises(PolicyError, match="floor"):
        governance._assert_pack_floor(PolicyPack(jurisdiction="IN", floor={"redaction": False}))


def _pack_version(code: str) -> int:
    with session_mod.session_scope() as s:
        return repo.current_pack_version(s, code)
