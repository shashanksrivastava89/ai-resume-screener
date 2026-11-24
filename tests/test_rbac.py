"""RBAC: the capability matrix + identity resolution.

P25 seeded the Addendum-08 four-role matrix; P36 extended it to six roles with the
capability UNION; P51 added the seventh role (Legal) and the scoring-governance
capabilities. The 2026-07-19 role pass reshaped the grants: SuperAdmin is the
SUPERSET (everything, from every user), HR Head sees all reqs but not Access &
Roles / feature flags, TA/HR Specialists are scoped to their OWN requisitions
(reqs_all is the new all-reqs lens), IT is turned off, and the local-dev default
is SuperAdmin as arjun.test@example.com."""

from __future__ import annotations

import pytest

from resume_screener import rbac
from tests.test_sso_identity import principal

RECRUITER_ROLES = ["SuperAdmin", "HRHead", "TASpecialist", "HRSpecialist"]


# --- the seven-role matrix -----------------------------------------------------------


def test_seven_roles_in_display_precedence():
    assert rbac.ROLES == ["SuperAdmin", "HRHead", "Legal", "IT", "TASpecialist",
                          "HRSpecialist", "HiringManager"]


def test_capability_matrix_matches_the_role_pass():
    m = rbac.load_rbac("rbac.yaml")
    # HR Head: all reqs + approvals — but NO Access & Roles, NO feature flags.
    assert rbac.can("HRHead", rbac.RECRUITER, m) and rbac.can("HRHead", rbac.REQS_ALL, m)
    assert rbac.can("HRHead", rbac.RULES_EDIT, m) and rbac.can("HRHead", rbac.PREFS_APPROVE, m)
    assert rbac.can("HRHead", rbac.SCORING_EDIT, m) and rbac.can("HRHead", rbac.CHANGES_APPROVE, m)
    assert not rbac.can("HRHead", rbac.ACCESS_VIEW, m)
    assert not rbac.can("HRHead", rbac.FLAGS_VIEW, m) and not rbac.can("HRHead", rbac.FLAGS_EDIT, m)
    # 2026-07-19: destructive data deletion stays with the Super Admin.
    assert not rbac.can("HRHead", rbac.DATA_MANAGE, m)
    # IT is OFF for now — zero capabilities (SuperAdmin covers the ground).
    assert rbac.capabilities("IT", m) == set()
    # The recruiter roles all screen + own their preferences…
    for role in RECRUITER_ROLES:
        assert rbac.can(role, rbac.RECRUITER, m)
        assert rbac.can(role, rbac.PREFS_OWN, m)
    # …but only HR Head + SuperAdmin see EVERY requisition.
    assert not rbac.can("TASpecialist", rbac.REQS_ALL, m)
    assert not rbac.can("HRSpecialist", rbac.REQS_ALL, m)
    assert not rbac.can("TASpecialist", rbac.RULES_VIEW, m)
    assert not rbac.can("HRSpecialist", rbac.RUNS_AUDIT, m)


def test_hiring_manager_takes_but_never_recruits():
    m = rbac.load_rbac("rbac.yaml")
    assert rbac.can("HiringManager", rbac.HM_TAKE, m)
    # 2026-07-19: NO prefs_own — preferences apply to the runs of the person who
    # screens, and an HM never screens; the surface would only mislead them.
    assert not rbac.can("HiringManager", rbac.PREFS_OWN, m)
    # No screening/deciding, no governance.
    assert not rbac.can("HiringManager", rbac.RECRUITER, m)
    assert not rbac.has_admin("HiringManager", m)


def test_super_admin_is_the_superset_except_every_requisitions_decisions():
    m = rbac.load_rbac("rbac.yaml")
    for cap in (rbac.RECRUITER, rbac.REQS_ALL, rbac.RULES_VIEW, rbac.RULES_EDIT,
                rbac.SCORING_EDIT, rbac.CHANGES_APPROVE, rbac.RUNS_AUDIT,
                rbac.DATA_MANAGE, rbac.PREFS_OWN, rbac.PREFS_APPROVE,
                rbac.ACCESS_VIEW, rbac.FLAGS_VIEW, rbac.FLAGS_EDIT, rbac.HM_TAKE,
                rbac.VERDICT_VIEW):
        assert rbac.can("SuperAdmin", cap, m), f"SuperAdmin missing {cap}"
    # Every other role's grant is a subset of the SuperAdmin's — EXCEPT the
    # decisions on every requisition, which stay with HR Heads (2026-10-21). An
    # administrator sees the decisions on a requisition of their own.
    superset = rbac.capabilities("SuperAdmin", m) | {rbac.DECISIONS_ALL}
    for role in rbac.ROLES:
        assert rbac.capabilities(role, m) <= superset, f"{role} exceeds SuperAdmin"
    assert not rbac.can("SuperAdmin", rbac.DECISIONS_ALL, m)
    assert rbac.has_admin("SuperAdmin", m)


def test_every_role_that_opens_candidates_sees_the_ai_verdict():
    """2026-10-21 (owner's decision): the AI's read on candidates is for everyone who
    can open them; HR-only from 2026-10-14. IT opens nothing, so holds nothing."""
    m = rbac.load_rbac("rbac.yaml")
    holders = {role for role in rbac.ROLES if rbac.can(role, rbac.VERDICT_VIEW, m)}
    assert holders == {"SuperAdmin", "HRHead", "Legal", "TASpecialist", "HRSpecialist",
                       "HiringManager"}
    assert not rbac.may_view_verdict(rbac.capabilities_for(["IT"], m))


def test_only_hr_heads_see_the_decisions_on_every_requisition():
    m = rbac.load_rbac("rbac.yaml")
    holders = {role for role in rbac.ROLES if rbac.can(role, rbac.DECISIONS_ALL, m)}
    assert holders == {"HRHead"}
    assert rbac.sees_all_decisions(rbac.capabilities_for(["SuperAdmin", "HRHead"], m))
    assert not rbac.sees_all_decisions(rbac.capabilities_for(["SuperAdmin", "Legal"], m))


def test_a_verdict_without_a_way_in_is_no_capability(monkeypatch):
    """A hiring manager whose portal is off keeps nothing — so they still get the
    "access not configured" page rather than one they cannot use."""
    from resume_screener import feature_flags

    monkeypatch.setattr(feature_flags, "flag_enabled", lambda key, region=None: False)
    assert rbac.effective_capabilities(["HiringManager"]) == set()


def test_the_shipped_matrix_and_the_fallback_agree():
    m = rbac.load_rbac("rbac.yaml")
    assert {r: set(c) for r, c in m.items()} == \
        {r: set(c) for r, c in rbac._DEFAULT_MATRIX.items()}


def test_a_capability_the_deployment_file_never_mentions_takes_the_builds_grant(tmp_path):
    """DATA_ROOT seeds rbac.yaml once and never overwrites it. A deployment whose file
    predates a capability must still get the build's grant for it — UAT's file
    predates access_edit and verdict_view — while anything the file DOES mention
    stays exactly as the deployment wrote it."""
    old = tmp_path / "rbac.yaml"
    old.write_text(
        "roles:\n"
        "  SuperAdmin: [recruiter, reqs_all, rules_view, flags_view, data_manage]\n"
        "  HRHead: [recruiter, reqs_all]\n"
        "  Legal: [rules_view]\n"
        "  TASpecialist: [recruiter]\n", encoding="utf-8")
    m = rbac.load_rbac(old)
    # New to this file: granted exactly as the build grants them — which is how
    # UAT's 2026-08 file picks up the 2026-10-21 grants without anyone editing it.
    assert rbac.ACCESS_EDIT in m["SuperAdmin"]
    assert rbac.VERDICT_VIEW in m["HRHead"] and rbac.VERDICT_VIEW in m["TASpecialist"]
    assert rbac.VERDICT_VIEW in m["HRSpecialist"]   # a role the file never listed
    assert rbac.VERDICT_VIEW in m["SuperAdmin"] and rbac.VERDICT_VIEW in m["Legal"]
    assert rbac.DECISIONS_ALL in m["HRHead"] and rbac.DECISIONS_ALL not in m["SuperAdmin"]
    # Mentioned by the file: the deployment's decision, left as written.
    assert rbac.DATA_MANAGE not in m["HRHead"]
    assert m["Legal"].count(rbac.RULES_VIEW) == 1


def test_withheld_switches_a_new_capability_off_everywhere(tmp_path):
    f = tmp_path / "rbac.yaml"
    f.write_text("roles:\n  HRHead: [recruiter]\nwithheld: [verdict_view]\n",
                 encoding="utf-8")
    m = rbac.load_rbac(f)
    assert not any(rbac.VERDICT_VIEW in caps for caps in m.values())


def test_legal_sees_approvals_audit_scoring_jurisdictions_only():
    m = rbac.load_rbac("rbac.yaml")
    assert rbac.can("Legal", rbac.RULES_VIEW, m) and rbac.can("Legal", rbac.RULES_EDIT, m)
    assert rbac.can("Legal", rbac.SCORING_EDIT, m) and rbac.can("Legal", rbac.CHANGES_APPROVE, m)
    assert rbac.can("Legal", rbac.RUNS_AUDIT, m)
    # READ-ONLY requisitions/workspace (2026-07-21) — reqs_read but never
    # recruiter (no writes) nor reqs_all (that's the full-write grant).
    assert rbac.can("Legal", rbac.REQS_READ, m)
    assert not rbac.can("Legal", rbac.RECRUITER, m)
    assert not rbac.can("Legal", rbac.REQS_ALL, m)
    # No preferences, no data/flags/access.
    assert not rbac.can("Legal", rbac.PREFS_OWN, m)
    assert not rbac.can("Legal", rbac.DATA_MANAGE, m)
    assert not rbac.can("Legal", rbac.ACCESS_VIEW, m)
    assert not rbac.can("Legal", rbac.FLAGS_VIEW, m)
    assert rbac.has_admin("Legal", m)


def test_admin_visibility_by_role():
    m = rbac.load_rbac("rbac.yaml")
    assert rbac.has_admin("HRHead", m) and rbac.has_admin("SuperAdmin", m)
    assert not rbac.has_admin("IT", m)   # zero caps => no Governance
    assert not rbac.has_admin("TASpecialist", m)
    assert not rbac.has_admin("HRSpecialist", m)


# --- the capability union (P36) ----------------------------------------------------


def test_union_across_held_roles():
    m = rbac.load_rbac("rbac.yaml")
    caps = rbac.capabilities_for(["TASpecialist", "Legal"], m)
    # A TA Specialist who is also Legal can screen AND approve config changes.
    assert rbac.RECRUITER in caps and rbac.CHANGES_APPROVE in caps
    assert rbac.PREFS_OWN in caps    # from TASpecialist
    assert rbac.SCORING_EDIT in caps  # from Legal
    assert rbac.REQS_ALL not in caps  # neither role grants the all-reqs lens
    assert rbac.PREFS_APPROVE not in caps


def test_union_ignores_unknown_roles():
    m = rbac.load_rbac("rbac.yaml")
    assert rbac.capabilities_for(["Nonexistent", "SuperAdmin"], m) == \
        rbac.capabilities("SuperAdmin", m)
    assert rbac.capabilities_for(["Nonexistent"], m) == set()
    assert rbac.capabilities_for([], m) == set()


def test_has_admin_caps_from_union():
    assert rbac.has_admin_caps({rbac.RECRUITER, rbac.FLAGS_VIEW})
    assert not rbac.has_admin_caps({rbac.RECRUITER, rbac.PREFS_OWN})


# --- identity: display role + full role list --------------------------------------


def test_identity_reads_dev_env(monkeypatch):
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DEV_USER", raising=False)
    # Local dev default (2026-07-19): the Arjun · Admin test persona.
    assert rbac.get_identity() == ("arjun.test@example.com", "SuperAdmin")
    monkeypatch.setenv("DEV_ROLE", "TASpecialist")
    monkeypatch.setenv("DEV_USER", "bhavika.test@example.com")
    assert rbac.get_identity() == ("bhavika.test@example.com", "TASpecialist")


def test_dev_role_accepts_comma_list(monkeypatch):
    monkeypatch.setenv("DEV_USER", "multi.test@example.com")
    monkeypatch.setenv("DEV_ROLE", "TASpecialist,SuperAdmin")
    user, display, roles = rbac.get_identity_full()
    assert user == "multi.test@example.com"
    assert display == "SuperAdmin"  # most-capable held, in ROLES order
    assert roles == ["SuperAdmin", "TASpecialist"]
    caps = rbac.capabilities_for(roles)
    assert rbac.RECRUITER in caps and rbac.FLAGS_EDIT in caps


def test_identity_full_from_principal_holds_all_known_roles():
    headers = {rbac.PRINCIPAL_HEADER: principal(
        "boss@example.com", ["SuperAdmin", "HRHead", "Nonexistent"])}
    user, display, roles = rbac.get_identity_full(headers)
    assert user == "boss@example.com"
    assert display == "SuperAdmin"  # most-capable
    assert roles == ["SuperAdmin", "HRHead"]  # ROLES order, unknown dropped


def test_unknown_role_has_no_capabilities():
    m = rbac.load_rbac("rbac.yaml")
    assert rbac.capabilities("Nonexistent", m) == set()
    assert not rbac.has_admin("Nonexistent", m)


# --- API enforcement: the union + the own-req lens in action ------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import shutil
    from pathlib import Path

    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    repo = Path(__file__).resolve().parents[1]
    shutil.copyfile(repo / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((repo / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return TestClient(app)


def _hdr(*roles: str, email: str = "user@example.com") -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL": principal(email, list(roles))}


def test_legal_cannot_create_roles(client):
    # Legal holds no recruiter capability — requisitions are off-limits to WRITE.
    response = client.post("/api/roles", headers=_hdr("Legal"),
                           json={"title": "X", "jd_text": "y", "icims_req_id": "R1"})
    assert response.status_code == 403


def test_legal_reads_workspace_read_only(client):
    """Legal (reqs_read) sees requisitions + the workspace for audit, but every
    write is refused (2026-07-21)."""
    mk = client.post("/api/roles", headers=_hdr("HRHead"),
                     json={"title": "Analyst", "jd_text": "GL accounting",
                           "icims_req_id": "R-9", "job_location": "IN"})
    assert mk.status_code == 200, mk.text
    rid = mk.json()["req_id"]

    # READS: list, role detail, batch — all open to Legal
    assert client.get("/api/roles", headers=_hdr("Legal")).status_code == 200
    assert client.get(f"/api/roles/{rid}", headers=_hdr("Legal")).status_code == 200
    assert client.get(f"/api/roles/{rid}/batch", headers=_hdr("Legal")).status_code == 200

    # WRITES: every one is 403 — Legal looks, never touches
    assert client.post(f"/api/roles/{rid}/decisions", headers=_hdr("Legal"),
                       json={"candidate_id": "c", "action": "forward"}).status_code == 403
    assert client.put(f"/api/roles/{rid}/rubric", headers=_hdr("Legal"),
                      json={"requirements": [{"text": "x", "kind": "must_have", "importance": 3}]}).status_code == 403
    assert client.post(f"/api/roles/{rid}/candidates/c/comments", headers=_hdr("Legal"),
                       json={"text": "hi"}).status_code == 403
    assert client.post(f"/api/roles/{rid}/screen", headers=_hdr("Legal")).status_code == 403
    assert client.post(f"/api/roles/{rid}/lock", headers=_hdr("Legal"),
                       json={"target": "checklist"}).status_code == 403


def test_it_is_turned_off(client):
    # IT has zero capabilities: every surface answers 403 with the setup message.
    assert client.get("/api/roles", headers=_hdr("IT")).status_code == 403
    assert client.get("/api/rbac", headers=_hdr("IT")).status_code == 403
    assert client.get("/api/audit/feed", headers=_hdr("IT")).status_code == 403


def test_hiring_manager_cannot_decide(client):
    response = client.post("/api/roles/any-role/decisions", headers=_hdr("HiringManager"),
                           json={"candidate_id": "c1", "action": "forward"})
    assert response.status_code == 403


def test_me_exposes_the_role_union(client):
    me = client.get("/api/me", headers=_hdr("TASpecialist", "Legal")).json()
    assert me["role"] == "Legal"  # most-capable in ROLES order
    assert sorted(me["roles"]) == ["Legal", "TASpecialist"]
    assert rbac.RECRUITER in me["capabilities"] and rbac.CHANGES_APPROVE in me["capabilities"]
    assert me["known_role"] is True and me["has_admin"] is True


def test_rbac_matrix_is_super_admin_only(client):
    # Access & Roles (2026-07-19): SuperAdmin only — the HR Head no longer sees it.
    assert client.get("/api/rbac", headers=_hdr("HRHead")).status_code == 403
    assert client.get("/api/rbac", headers=_hdr("Legal")).status_code == 403
    data = client.get("/api/rbac", headers=_hdr("SuperAdmin")).json()
    assert [r["name"] for r in data["rows"]] == rbac.ROLES
    cap_keys = {c["key"] for c in data["caps"]}
    assert {rbac.REQS_ALL, rbac.ACCESS_VIEW, rbac.FLAGS_EDIT, rbac.PREFS_APPROVE,
            rbac.HM_TAKE, rbac.SCORING_EDIT, rbac.CHANGES_APPROVE} <= cap_keys


def test_own_req_scoping(client):
    """The 2026-07-19 lens: a TA sees/opens only their own reqs; the HR Head and
    the SuperAdmin (reqs_all) see everything."""
    ta1 = _hdr("TASpecialist", email="bhavika.test@example.com")
    ta2 = _hdr("TASpecialist", email="meera.test@example.com")
    made = client.post("/api/roles", headers=ta1,
                       json={"title": "Bhavika's Req", "jd_text": "Owns treasury.",
                             "job_location": "IN", "icims_req_id": "R-100"})
    assert made.status_code == 200, made.text
    req_id = made.json()["req_id"]

    # The creator sees + opens it.
    assert any(r["req_id"] == req_id for r in
               client.get("/api/roles", headers=ta1).json()["roles"])
    assert client.get(f"/api/roles/{req_id}", headers=ta1).status_code == 200

    # Another specialist neither lists nor opens it.
    assert not any(r["req_id"] == req_id for r in
                   client.get("/api/roles", headers=ta2).json()["roles"])
    denied = client.get(f"/api/roles/{req_id}", headers=ta2)
    assert denied.status_code == 403
    assert "another recruiter" in denied.json()["detail"]

    # reqs_all holders see and open everything.
    for boss in (_hdr("HRHead", email="hrhead.test@example.com"),
                 _hdr("SuperAdmin", email="arjun.test@example.com")):
        assert any(r["req_id"] == req_id for r in
                   client.get("/api/roles", headers=boss).json()["roles"])
        assert client.get(f"/api/roles/{req_id}", headers=boss).status_code == 200
