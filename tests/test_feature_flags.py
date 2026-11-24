"""P37: file-first feature flags — resolution, the DB-wins merge, publishing
(version bump + audit + event), env-controlled flags, capability suppression, and
the flag-gated Reopen/Clone routes. Offline: file store on tmp_path, DB merge on
in-memory SQLite.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine
from tests.packdata import set_flag_defaults
from tests.test_sso_identity import principal

from resume_screener import feature_flags, rbac

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def flags_ws(tmp_path, monkeypatch):
    """A workspace with a fresh copy of features.yaml so set_flag writes never touch
    the committed seed; signals/ also catches the audit log + events spine."""
    monkeypatch.delenv("FEATURE_HM_PORTAL", raising=False)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    # These tests reason from "the iCIMS flags are OFF"; say so in the copy rather
    # than trusting the catalog's current toggle (a SuperAdmin flips it at will).
    set_flag_defaults(tmp_path / "signals" / "features.yaml",
                      {"icims_reopen": False, "icims_clone": False})
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _read_yaml() -> dict:
    return yaml.safe_load(Path("signals/features.yaml").read_text(encoding="utf-8"))


# --- resolution: default / region override / env ----------------------------------


def test_default_and_region_override_precedence(flags_ws):
    # The fixture's default is off everywhere.
    assert feature_flags.flag_enabled("icims_reopen") is False
    assert feature_flags.flag_enabled("icims_reopen", "IN") is False
    # A region override wins for that region only.
    feature_flags.set_flag("icims_reopen", "IN", True, author="head@example.com")
    assert feature_flags.flag_enabled("icims_reopen", "IN") is True
    assert feature_flags.flag_enabled("icims_reopen", "EMEA") is False
    assert feature_flags.flag_enabled("icims_reopen") is False  # global default untouched
    # The global default flips independently of the region override.
    feature_flags.set_flag("icims_reopen", None, True, author="head@example.com")
    assert feature_flags.flag_enabled("icims_reopen", "EMEA") is True  # falls back to default


def test_unknown_flag_is_off(flags_ws):
    assert feature_flags.flag_enabled("does_not_exist") is False


# --- env-controlled flags ----------------------------------------------------------


def test_an_env_controlled_flag_tracks_its_env_var(flags_ws, monkeypatch):
    monkeypatch.delenv("FEATURE_CAREERS_AUTOFILL", raising=False)
    assert feature_flags.flag_enabled("careers_autofill") is False
    monkeypatch.setenv("FEATURE_CAREERS_AUTOFILL", "1")
    assert feature_flags.flag_enabled("careers_autofill") is True
    monkeypatch.setenv("FEATURE_CAREERS_AUTOFILL", "0")
    assert feature_flags.flag_enabled("careers_autofill") is False


def test_set_flag_rejects_env_controlled(flags_ws):
    with pytest.raises(feature_flags.FeatureError) as exc:
        feature_flags.set_flag("careers_autofill", None, True, author="head@example.com")
    assert "deployment" in str(exc.value)


def test_hm_portal_is_toggleable_from_the_app(flags_ws, monkeypatch):
    """2026-09-07: it used to be env-controlled, so lighting up the hiring-manager
    portal meant an app-settings change and a restart. A SuperAdmin owns it now, and
    the stale FEATURE_HM_PORTAL env var must not quietly override them."""
    monkeypatch.setenv("FEATURE_HM_PORTAL", "0")   # a leftover deployment setting
    assert feature_flags.flag_enabled("hm_portal") is True   # the file default wins
    feature_flags.set_flag("hm_portal", None, False, author="sa@example.com")
    assert feature_flags.flag_enabled("hm_portal") is False
    # …and it is APP-scoped (2026-09-12): capability suppression reads it with no
    # requisition in hand, so a per-region override could never be consulted.
    # Setting one used to be accepted and do nothing; it is refused now, and asking
    # about a region gets the global answer rather than a per-region fiction.
    with pytest.raises(feature_flags.FeatureError):
        feature_flags.set_flag("hm_portal", "IN", True, author="sa@example.com")
    assert feature_flags.flag_enabled("hm_portal", "IN") is False


# --- publishing: version bump + audit + event -------------------------------------


def test_set_flag_bumps_version_audits_and_emits(flags_ws):
    from resume_screener import events, governance

    before = _read_yaml()["version"]
    feature_flags.set_flag("icims_clone", None, True, author="it@example.com")
    after = _read_yaml()["version"]
    assert after == before + 1

    audit = governance.load_audit()
    assert audit and audit[-1]["action"] == "publish_flags"
    assert audit[-1]["target"] == "icims_clone" and audit[-1]["author"] == "it@example.com"

    changed = events.load_events(kinds=["flag_changed"])
    assert len(changed) == 1
    assert changed[0]["data"] == {"key": "icims_clone", "region": None,
                                  "enabled": True, "version": after}


def test_catalog_has_all_seed_keys_with_control_class(flags_ws):
    cat = {f["key"]: f for f in feature_flags.catalog()}
    assert {"icims_reopen", "icims_clone", "icims_close_sync", "hm_portal",
            "ask_claude_mcp", "ask_claude_handoff"} <= set(cat)
    # Both ways candidate data can reach Claude ship switched off (2026-10-14/23).
    assert cat["ask_claude_mcp"]["default"] is False
    assert cat["ask_claude_handoff"]["default"] is False
    assert cat["ask_claude_handoff"]["scope"] == "app"
    # careers_autofill is the deployment-owned one now; hm_portal joined the
    # ordinary flags on 2026-09-07 so a SuperAdmin can turn the portal on.
    assert cat["careers_autofill"].get("control") == "env"
    assert cat["careers_autofill"]["env"] == "FEATURE_CAREERS_AUTOFILL"
    assert "control" not in cat["icims_reopen"]
    assert cat["hm_portal"].get("control") != "env"


# --- the DB-wins-per-key merge (SQLite) -------------------------------------------


@pytest.fixture()
def db_backend(monkeypatch):
    from resume_screener.db import engine as engine_mod
    from resume_screener.db import session as session_mod
    from resume_screener.db.repositories import regions as regions_repo
    from resume_screener.db.seed import seed_region_framework

    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    with session_mod.session_scope() as s:
        seed_region_framework(s, dev_user="local-user", dev_role="HRHead")
        # A DB row for a key the FILE also defines, with a deliberate override in
        # each direction — and EMEA left silent, so inheritance is covered too.
        regions_repo.upsert_feature(s, "icims_reopen", "Reopen (DB)",
                                     description="db description")
        regions_repo.set_region_feature(s, "IN", "icims_reopen", True)
        regions_repo.set_region_feature(s, "AMER", "icims_reopen", False)
    try:
        yield
    finally:
        eng.dispose()


def test_db_wins_per_key_merge(db_backend):
    cat = {f["key"]: f for f in feature_flags.catalog()}
    # The DB row wins for icims_reopen (name/description/regions from the DB), and
    # `regions` carries ONLY the regions that actually overrode it. EMEA is absent
    # because it never spoke — not because it is off.
    assert cat["icims_reopen"]["source"] == "db"
    assert cat["icims_reopen"]["regions"] == {"IN": True, "AMER": False}
    # File-only keys still come from the file.
    assert cat["icims_clone"]["source"] == "file"
    # DB-only seed keys are present too (union).
    assert "blind_review" in cat
    # flag_enabled resolves through the DB regions for that key…
    assert feature_flags.flag_enabled("icims_reopen", "IN") is True
    assert feature_flags.flag_enabled("icims_reopen", "AMER") is False
    # …and a silent region inherits the flag's default (which is ON for this one).
    assert feature_flags.flag_enabled("icims_reopen", "EMEA") is True


# --- capability suppression --------------------------------------------------------


def test_capabilities_drop_hm_take_when_portal_off(flags_ws):
    """Capability suppression follows the FLAG, whatever turns it (2026-09-07: it is
    a SuperAdmin toggle now, not an env var)."""
    assert rbac.HM_TAKE in rbac.effective_capabilities(["HiringManager"])  # default on

    feature_flags.set_flag("hm_portal", None, False, author="sa@example.com")
    caps_off = rbac.effective_capabilities(["HiringManager"])
    assert rbac.HM_TAKE not in caps_off
    # 2026-07-19: prefs_own was removed from HiringManager (preferences apply to
    # the runs of whoever screens — never the HM), so portal-off = zero caps.
    assert caps_off == set()
    # The raw matrix union still lists hm_take — suppression is only in the effective set.
    assert rbac.HM_TAKE in rbac.capabilities_for(["HiringManager"])


# --- the flag-gated routes (Reopen / Clone) ---------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("FEATURE_HM_PORTAL", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    set_flag_defaults(tmp_path / "signals" / "features.yaml",
                      {"icims_reopen": False, "icims_clone": False})  # the routes' "off" case
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return TestClient(app)


def _as(role: str) -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL": principal(f"{role.lower()}@example.com", [role])}


def _make_req(client, title="Treasury Analyst", location="IN") -> str:
    created = client.post("/api/roles", headers=_as("HRHead"), json={
        "title": title, "jd_text": "Owns treasury accounting and month-end close.",
        "job_location": location, "icims_req_id": "REQ-1"})
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def test_reopen_403_when_flag_off(client):
    req_id = _make_req(client)
    client.post(f"/api/roles/{req_id}/close", headers=_as("HRHead"),
                json={"outcome": "cancelled"})
    blocked = client.post(f"/api/roles/{req_id}/reopen", headers=_as("HRHead"))
    assert blocked.status_code == 403 and "feature flag" in blocked.json()["detail"]
    # A Super Admin turns it on (global default) → the recruiter's reopen works.
    assert client.put("/api/flags/icims_reopen", headers=_as("SuperAdmin"),
                      json={"enabled": True}).status_code == 200
    ok = client.post(f"/api/roles/{req_id}/reopen", headers=_as("HRHead"))
    assert ok.status_code == 200 and ok.json()["status"] == "active"


def test_clone_403_when_flag_off_then_prefill_shape(client):
    req_id = _make_req(client)
    # A checklist so the clone prefill carries requirements.
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("HRHead"), json={
        "requirements": [{"text": "US GAAP", "kind": "must_have", "importance": 4},
                         {"text": "Must be a CA", "kind": "hard_gate"}]})

    blocked = client.post(f"/api/roles/{req_id}/clone", headers=_as("HRHead"))
    assert blocked.status_code == 403 and "feature flag" in blocked.json()["detail"]

    assert client.put("/api/flags/icims_clone", headers=_as("SuperAdmin"),
                      json={"enabled": True}).status_code == 200
    resp = client.post(f"/api/roles/{req_id}/clone", headers=_as("HRHead"))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"title", "jd_text", "job_location", "country",
                         "us_state", "checklist"}
    assert body["job_location"] == "IN"
    assert "Owns treasury accounting" in body["jd_text"]
    texts = {r["text"] for r in body["checklist"]}
    assert {"US GAAP", "Must be a CA"} <= texts
    # Fresh rows — no source ids carried into the new checklist.
    assert all("id" not in r for r in body["checklist"])


def test_put_flag_rejects_env_controlled(client):
    resp = client.put("/api/flags/careers_autofill", headers=_as("SuperAdmin"),
                      json={"enabled": True})
    assert resp.status_code == 400 and "deployment" in resp.json()["detail"]


def test_put_flag_turns_the_hm_portal_on_and_off(client):
    """The point of the change: no app-settings edit, no restart."""
    off = client.put("/api/flags/hm_portal", headers=_as("SuperAdmin"), json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["flag"]["default"] is False
    on = client.put("/api/flags/hm_portal", headers=_as("SuperAdmin"), json={"enabled": True})
    assert on.status_code == 200 and on.json()["flag"]["enabled"] is True


def test_put_flag_needs_flags_edit(client):
    # HR Head can VIEW flags (flags_view) but only a Super Admin holds flags_edit.
    resp = client.put("/api/flags/icims_reopen", headers=_as("HRHead"),
                      json={"enabled": True})
    assert resp.status_code == 403


def test_features_readable_by_recruiter(client):
    resp = client.get("/api/features", headers=_as("TASpecialist"))
    assert resp.status_code == 200
    keys = {f["key"] for f in resp.json()["features"]}
    assert "icims_reopen" in keys and "hm_portal" in keys


# --- flag DEFINITIONS follow the build, values follow the deployment ------------------


def test_definitions_follow_the_build_over_a_stale_stored_file(flags_ws):
    """DATA_ROOT seeds features.yaml into an empty root and never overwrites it, so a
    hosted deployment keeps whatever it was first seeded with. A release that renames a
    flag — or hands one over from the deployment to the app — has to reach it anyway
    (2026-09-07: the hiring-manager portal kept rendering "controlled by deployment"
    after the release that made it a Super Admin's toggle)."""
    import yaml

    from resume_screener import feature_flags

    path = Path("signals/features.yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    # An old share: hm_portal still deployment-owned and off, and a flag this build
    # has since added is missing entirely.
    doc["features"]["hm_portal"] = {"name": "Hiring-manager portal (old)",
                                    "description": "old text", "default": False,
                                    "control": "env", "env": "FEATURE_HM_PORTAL"}
    doc["features"].pop("ask_claude_mcp", None)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    cat = {f["key"]: f for f in feature_flags.catalog()}
    # the definition comes from the build: editable here, current wording
    assert cat["hm_portal"].get("control") != "env"
    assert cat["hm_portal"]["name"] == "Hiring-manager portal"
    # ...and because nobody could ever set that value in the app, the build's default
    # applies rather than a stale off that would darken the feature on release day
    assert cat["hm_portal"]["default"] is True
    # a flag the build added since the share was seeded simply appears
    assert "ask_claude_mcp" in cat
    # and it is genuinely toggleable now
    feature_flags.set_flag("hm_portal", None, False, author="sa@example.com")
    assert feature_flags.flag_enabled("hm_portal") is False


def test_a_value_a_human_set_survives_a_release(flags_ws):
    """The other half of the rule: values the app CAN set are the deployment's, and a
    build must never quietly reset them."""
    from resume_screener import feature_flags

    feature_flags.set_flag("icims_clone", None, True, author="sa@example.com")
    cat = {f["key"]: f for f in feature_flags.catalog()}
    assert cat["icims_clone"]["default"] is True   # not clobbered by the build's False


# --- a flag says whether it CAN differ per region (2026-09-12) ----------------------


def test_every_shipped_flag_declares_a_valid_scope(flags_ws):
    """Missing would resolve to "app", the safe direction — but a flag nobody has
    thought about is exactly the one that ends up promising a control it cannot
    honour, so the catalog states it outright."""
    for entry in feature_flags.catalog():
        assert entry.get("scope") in feature_flags.SCOPES, entry["key"]


def test_a_requisition_scoped_flag_still_takes_region_overrides(flags_ws):
    """Whatever the default is in this workspace, one region can differ from it."""
    default = feature_flags.flag_enabled("icims_reopen")
    feature_flags.set_flag("icims_reopen", "IN", not default, author="sa@example.com")
    assert feature_flags.flag_enabled("icims_reopen", "IN") is (not default)
    assert feature_flags.flag_enabled("icims_reopen", "EMEA") is default


def test_an_app_scoped_flag_refuses_a_region_override(flags_ws):
    with pytest.raises(feature_flags.FeatureError) as exc:
        feature_flags.set_flag("manual_locks", "IN", True, author="sa@example.com")
    assert "applies everywhere" in str(exc.value)


def test_the_route_refuses_it_too_with_the_same_words(client):
    refused = client.put("/api/flags/manual_locks",
                         headers=_as("SuperAdmin"), json={"enabled": True, "region": "IN"})
    assert refused.status_code == 400
    assert "applies everywhere" in refused.json()["detail"]
