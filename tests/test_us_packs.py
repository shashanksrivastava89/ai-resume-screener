"""P24: per-role job location + the US policy packs (Concord extension).

The load-bearing check: the SAME role flipped IN -> US-CA changes the enabled
rules' params/caps and the obligations list, while the floor is untouched in both.
Plus the per-role location sidecar (save/load) and the ATS reading it."""

from __future__ import annotations

from pathlib import Path

from resume_screener.ats.local_folder import (
    LocalFolderATS,
    load_req_meta,
    req_meta_path,
    save_req_meta,
)
from resume_screener.policy import FLOOR_ITEMS, resolve_policy
from resume_screener.registry import apply_policy, load_registry
from tests.packdata import pack_days


def _caps(policy):
    registry = load_registry("signals/registry.yaml")
    return {s.id: s.cap_points for s in apply_policy(registry, policy)[0]}


# --- item 4: flipping a role IN -> US-CA changes rules/params + obligations; floor same ---


def test_flip_in_to_us_ca_diverges_but_floor_is_identical():
    in_ = resolve_policy("IN")
    ca = resolve_policy("US-CA")

    # tenure params differ (IN tunes short stints to 9 months; US-CA inherits base 12)
    assert in_.signal_overrides["tenure_pattern_vs_stability"].params["short_stint_months"] == 9
    assert ca.signal_overrides["tenure_pattern_vs_stability"].params["short_stint_months"] == 12

    # an enabled rule's effective cap differs (IN lifts language evidence to 3)
    assert _caps(in_)["language_evidence"] == 3
    assert _caps(ca)["language_evidence"] == 2

    # obligations + retention differ
    assert set(in_.obligations) != set(ca.obligations)
    assert (in_.keep_at_least_days, in_.purge_by_days) == pack_days("IN")
    assert (ca.keep_at_least_days, ca.purge_by_days) == pack_days("US-CA")
    assert pack_days("IN") != pack_days("US-CA")  # California's four-year record-keeping is its own regime

    # the floor is byte-for-byte the same and fully on in both
    assert in_.floor == ca.floor
    for item in FLOOR_ITEMS:
        assert in_.floor[item] is True and ca.floor[item] is True


def test_us_ca_obligations_cover_crd_and_ccpa():
    obligations = " ".join(resolve_policy("US-CA").obligations).lower()
    assert "crd" in obligations and "automated-decision" in obligations
    assert "ccpa" in obligations and "notice at collection" in obligations


def test_us_default_obligations_cover_eeoc_title_vii_ada():
    obligations = " ".join(resolve_policy("US-default").obligations).lower()
    assert "eeoc" in obligations and "title vii" in obligations and "ada" in obligations


def test_us_nyc_keeps_ll144_bias_audit_and_ten_day_notice():
    obligations = " ".join(resolve_policy("US-NYC").obligations).lower()
    assert "ll144" in obligations
    assert "bias audit" in obligations
    assert "10 business days" in obligations


def test_us_state_without_a_pack_falls_back_to_us_default():
    assert resolve_policy("US-CA").pack == "US-CA"      # own pack
    assert resolve_policy("US-TX").pack == "US-default"  # arbitrary state -> federal baseline


# --- per-role location in the .req.json sidecar (P29; default for sidecar-less roles) ---


def test_req_meta_location_round_trip(tmp_path):
    jd = tmp_path / "some-role.md"
    jd.write_text("# Some Role\n", encoding="utf-8")
    assert load_req_meta(jd) == {}  # sidecar-less role -> caller uses the default
    save_req_meta(jd, job_location="US-CA")
    assert req_meta_path(jd).name == "some-role.req.json"
    assert load_req_meta(jd)["job_location"] == "US-CA"


def test_ats_requisition_reads_the_sidecar_else_default(tmp_path):
    jd_dir = tmp_path / "jds"
    jd_dir.mkdir()
    (jd_dir / "legacy.md").write_text("# Legacy Role\n", encoding="utf-8")
    us_jd = jd_dir / "hercules.md"
    us_jd.write_text("# Concord Role\n", encoding="utf-8")
    save_req_meta(us_jd, job_location="US-CA")

    ats = LocalFolderATS(jd_dir=jd_dir, resumes_dir=tmp_path / "r",
                         default_job_location="IN", roles_dir=tmp_path / "roles")
    by_id = {r.req_id: r for r in ats.fetch_requisitions()}
    assert by_id["legacy"].job_location == "IN"      # no sidecar -> configured default
    assert by_id["hercules"].job_location == "US-CA"  # sidecar wins
