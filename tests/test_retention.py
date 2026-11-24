"""P45: the keep/purge retention model + the purge queue + legal holds.

VISIBILITY ONLY — nothing here deletes anything. Covers the two per-pack duties
(keep_at_least_days + purge_by_days) loading/merging, floor protection staying
intact, the purge-date math, a legal hold pausing a requisition's status, and a
publish_pack edit of both fields writing an audit entry.

Offline: tmp_path workspace (never real output/); the policy packs are the
committed seeds copied in.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
from tests.packdata import pack_days, plus_days

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mk_role(stem="finance-analyst", location="IN", **meta):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    save_req_meta(jd, job_location=location, **meta)
    return stem, jd


# --- pack fields load + merge -------------------------------------------------------


def test_pack_fields_load_and_merge(workspace):
    from resume_screener.policy import resolve_policy

    # Each overlay wins over the base defaults — the expected values are read from the
    # pack files themselves (governed data; see tests/packdata.py), and must be present.
    for code in ("IN", "EU", "US-CA"):
        eff = resolve_policy(code)
        assert (eff.keep_at_least_days, eff.purge_by_days) == pack_days(code), code
        assert eff.keep_at_least_days and eff.purge_by_days, code
    # the three packs still carry three DIFFERENT retention regimes
    assert len({pack_days(c) for c in ("IN", "EU", "US-CA")}) == 3
    # a US state with no pack falls back to US-default's values (fallback resolution)
    tx = resolve_policy("US-TX")
    assert tx.pack == "US-default"
    assert (tx.keep_at_least_days, tx.purge_by_days) == (730, 365)


def test_floor_protection_untouched(workspace):
    """The retention rewrite doesn't weaken the floor rail: an overlay that tries to
    turn a floor item off still fails validation."""
    from resume_screener.policy import PolicyError, resolve_policy

    (Path("policies") / "ZZ.yaml").write_text(
        "jurisdiction: ZZ\nfloor:\n  redaction: false\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="floor"):
        resolve_policy("ZZ")


# --- purge date math ----------------------------------------------------------------


def test_purge_queue_date_math(workspace):
    from resume_screener import retention

    _mk_role("closed-role", location="IN", status="closed",
             closed_at="2026-01-01T00:00:00+00:00")
    _mk_role("open-role", location="IN")

    rows = {r["req_id"]: r for r in retention.purge_queue()}

    closed = rows["closed-role"]
    keep, purge = pack_days("IN")
    assert closed["status"] == "scheduled"
    assert closed["purge_due"] == plus_days("2026-01-01T00:00:00+00:00", purge)         # closed_at + purge_by_days
    assert closed["keep_audit_until"] == plus_days("2026-01-01T00:00:00+00:00", keep)  # closed_at + keep_at_least_days
    assert closed["pack"] == "IN"

    open_row = rows["open-role"]
    assert open_row["status"] == "awaiting_close"
    assert open_row["purge_due"] is None


# --- legal holds --------------------------------------------------------------------


def test_hold_pauses_status_and_emits_events(workspace):
    from resume_screener import retention
    from resume_screener.events import load_events

    _mk_role("closed-role", location="IN", status="closed",
             closed_at="2026-01-01T00:00:00+00:00")

    retention.apply_hold("closed-role", by="hrhead@example.com", reason="litigation hold")
    row = next(r for r in retention.purge_queue() if r["req_id"] == "closed-role")
    assert row["status"] == "on_hold"
    assert row["hold"]["reason"] == "litigation hold"
    applied = load_events(req_id="closed-role", kinds=["legal_hold_applied"])
    assert applied and "litigation hold" in applied[-1]["summary"]

    retention.release_hold("closed-role", by="hrhead@example.com")
    row = next(r for r in retention.purge_queue() if r["req_id"] == "closed-role")
    assert row["status"] == "scheduled" and row["hold"] is None
    assert load_events(req_id="closed-role", kinds=["legal_hold_released"])


def test_hold_requires_a_reason(workspace):
    from resume_screener import retention

    _mk_role("closed-role", location="IN", status="closed",
             closed_at="2026-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="reason"):
        retention.apply_hold("closed-role", by="x@example.com", reason="  ")


# --- publish_pack edits both fields + audits ----------------------------------------


def test_publish_pack_edits_keep_and_purge(workspace, tmp_path):
    from resume_screener import governance
    from resume_screener.policy import resolve_policy

    log = tmp_path / "audit.jsonl"
    v0 = resolve_policy("IN")
    version = governance.publish_pack("IN", keep_at_least_days=1200, purge_by_days=200,
                                      author="hrhead@example.com",
                                      rationale="calibrate retention", log_path=log)
    eff = resolve_policy("IN")
    assert (eff.keep_at_least_days, eff.purge_by_days) == (1200, 200)
    audit = governance.load_audit(log)
    assert audit[-1]["action"] == "publish_pack" and audit[-1]["version"] == version
    assert "keep" in audit[-1]["summary"] and "purge" in audit[-1]["summary"]
    # floor untouched by the edit
    assert eff.floor == v0.floor
