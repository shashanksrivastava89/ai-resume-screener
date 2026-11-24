"""P27: Easy-Auth principal parsing + header-based identity + API RBAC enforcement.

The principal header is what Azure Container Apps authentication injects after a
successful Entra login: base64(JSON) with a claims list. These tests feed sample
payloads straight into the parser and through the FastAPI routes, so the SSO path
is proven offline — no Azure involved.
"""

from __future__ import annotations

import base64
import json

import pytest

from resume_screener import rbac


def principal(email: str | None = None, roles: list[str] | None = None,
              name: str | None = None, email_claim: str = "preferred_username",
              country: str | None = None) -> str:
    claims = []
    if email is not None:
        claims.append({"typ": email_claim, "val": email})
    if name is not None:
        claims.append({"typ": "name", "val": name})
    if country is not None:
        claims.append({"typ": "ctry", "val": country})
    for role in roles or []:
        claims.append({"typ": "roles", "val": role})
    payload = {"auth_typ": "aad", "claims": claims,
               "name_typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
               "role_typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"}
    return base64.b64encode(json.dumps(payload).encode()).decode()


# --- the parser --------------------------------------------------------------------


def test_parses_email_name_and_roles():
    parsed = rbac.parse_client_principal(
        principal("bhavika@example.com", ["TASpecialist"], name="Bhavika S"))
    assert parsed == {"email": "bhavika@example.com", "name": "Bhavika S",
                      "roles": ["TASpecialist"], "country": ""}


def test_the_country_claim_is_read_when_the_registration_emits_it():
    """`ctry` is an OPTIONAL claim on the app registration (2026-09-12). It opens
    someone's region picker somewhere sensible and decides nothing else, so its
    absence has to be as ordinary as its presence."""
    with_country = rbac.parse_client_principal(
        principal("bhavika@example.com", ["TASpecialist"], country="HU"))
    assert with_country["country"] == "HU"

    headers = {rbac.PRINCIPAL_HEADER: principal("p@example.com", [], country="DE")}
    assert rbac.principal_country(headers) == "DE"
    assert rbac.principal_country({}) == ""


def test_email_claim_fallbacks():
    upn = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn"
    parsed = rbac.parse_client_principal(
        principal("head@example.com", ["HRHead"], email_claim=upn))
    assert parsed["email"] == "head@example.com"


def test_long_form_role_claim_type():
    long_typ = "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"
    payload = {"claims": [{"typ": long_typ, "val": "IT"}]}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    assert rbac.parse_client_principal(encoded)["roles"] == ["IT"]


@pytest.mark.parametrize("garbage", [
    "not-base64!!!",
    base64.b64encode(b"not json").decode(),
    base64.b64encode(b'"a bare string"').decode(),
    base64.b64encode(b'{"claims": "not-a-list"}').decode(),
])
def test_malformed_header_degrades_to_none(garbage):
    assert rbac.parse_client_principal(garbage) is None


def test_effective_role_precedence_and_unknowns():
    # Most-capable known role wins; unknown Entra roles are ignored, not errors.
    assert rbac.role_from_app_roles(["HRSpecialist", "HRHead"]) == "HRHead"
    assert rbac.role_from_app_roles(["SomethingElse", "IT"]) == "IT"
    assert rbac.role_from_app_roles(["SomethingElse"]) == ""
    assert rbac.role_from_app_roles([]) == ""


# --- get_identity: header wins over env, env is the local fallback ------------------


def test_identity_prefers_principal_header(monkeypatch):
    monkeypatch.setenv("DEV_USER", "dev@local")
    monkeypatch.setenv("DEV_ROLE", "HRHead")
    headers = {rbac.PRINCIPAL_HEADER: principal("ta@example.com", ["TASpecialist"])}
    assert rbac.get_identity(headers) == ("ta@example.com", "TASpecialist")


def test_identity_uses_name_header_when_no_email_claim():
    headers = {
        rbac.PRINCIPAL_HEADER: principal(roles=["IT"]),
        rbac.PRINCIPAL_NAME_HEADER: "it@example.com",
    }
    assert rbac.get_identity(headers) == ("it@example.com", "IT")


def test_identity_falls_back_to_dev_env(monkeypatch):
    monkeypatch.setenv("DEV_USER", "dev@local")
    monkeypatch.setenv("DEV_ROLE", "IT")
    assert rbac.get_identity(None) == ("dev@local", "IT")
    assert rbac.get_identity({}) == ("dev@local", "IT")  # no header on the request


def test_authenticated_user_without_app_role_has_no_capabilities():
    headers = {rbac.PRINCIPAL_HEADER: principal("new@example.com", [])}
    user, role = rbac.get_identity(headers)
    assert user == "new@example.com" and role == ""
    assert rbac.capabilities(role) == set()


def test_get_current_user_helper(monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    # 2026-07-19: the dev default is the Arjun · Admin test persona.
    assert rbac.get_current_user(None) == "arjun.test@example.com"
    headers = {rbac.PRINCIPAL_HEADER: principal("head@example.com", ["HRHead"])}
    assert rbac.get_current_user(headers) == "head@example.com"


# --- dev user switching (X-Dev-User / X-Dev-Role, until SSO) -------------------------


def test_dev_headers_override_env(monkeypatch):
    monkeypatch.setenv("DEV_USER", "dev@local")
    monkeypatch.setenv("DEV_ROLE", "HRHead")
    headers = {rbac.DEV_USER_HEADER: "bhavika.test@example.com",
               rbac.DEV_ROLE_HEADER: "TASpecialist,SuperAdmin"}
    user, display, roles = rbac.get_identity_full(headers)
    assert user == "bhavika.test@example.com"
    assert display == "SuperAdmin"  # most-capable held (SuperAdmin leads ROLES)
    assert roles == ["SuperAdmin", "TASpecialist"]  # comma list = union, like env


def test_dev_headers_override_each_field_independently(monkeypatch):
    monkeypatch.setenv("DEV_USER", "dev@local")
    monkeypatch.setenv("DEV_ROLE", "IT")
    user, display, _ = rbac.get_identity_full({rbac.DEV_ROLE_HEADER: "HRSpecialist"})
    assert (user, display) == ("dev@local", "HRSpecialist")
    user, display, _ = rbac.get_identity_full({rbac.DEV_USER_HEADER: "ta@example.com"})
    assert (user, display) == ("ta@example.com", "IT")


def test_dev_headers_ignored_when_principal_present(monkeypatch):
    """Hosted requests carry the Easy-Auth principal — the switcher never
    overrides a real signed-in identity."""
    headers = {rbac.PRINCIPAL_HEADER: principal("real@example.com", ["IT"]),
               rbac.DEV_USER_HEADER: "spoof@example.com",
               rbac.DEV_ROLE_HEADER: "HRHead"}
    assert rbac.get_identity(headers) == ("real@example.com", "IT")


def test_dev_switch_kill_switch(monkeypatch):
    monkeypatch.setenv("DEV_SWITCH", "0")
    monkeypatch.setenv("DEV_USER", "dev@local")
    monkeypatch.setenv("DEV_ROLE", "IT")
    headers = {rbac.DEV_USER_HEADER: "bhavika@example.com",
               rbac.DEV_ROLE_HEADER: "HRHead"}
    assert rbac.get_identity(headers) == ("dev@local", "IT")
    assert rbac.dev_switch_active(headers) is False


def test_dev_switch_active_only_without_principal(monkeypatch):
    monkeypatch.delenv("DEV_SWITCH", raising=False)
    assert rbac.dev_switch_active({}) is True
    assert rbac.dev_switch_active(None) is True
    hosted = {rbac.PRINCIPAL_HEADER: principal("real@example.com", ["IT"])}
    assert rbac.dev_switch_active(hosted) is False


# --- enforcement on the API routes (Addendum 08 matrix) -----------------------------


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    return TestClient(app)


def _as(role: str) -> dict[str, str]:
    return {"X-MS-CLIENT-PRINCIPAL": principal(f"{role.lower()}@example.com", [role])}


def test_me_reflects_sso_identity(client):
    me = client.get("/api/me", headers=_as("TASpecialist")).json()
    assert me["user"] == "taspecialist@example.com"
    assert me["role"] == "TASpecialist"
    assert me["known_role"] is True
    assert me["has_admin"] is False


def test_me_flags_unassigned_user(client):
    headers = {"X-MS-CLIENT-PRINCIPAL": principal("new@example.com", [])}
    me = client.get("/api/me", headers=headers).json()
    assert me["known_role"] is False and me["capabilities"] == []


def test_me_dev_switch_flag(client, monkeypatch):
    monkeypatch.delenv("DEV_SWITCH", raising=False)
    # SSO principal present -> no switcher, ever
    assert client.get("/api/me", headers=_as("TASpecialist")).json()["dev_switch"] is False
    # dev path (no principal) -> the switcher is offered…
    assert client.get("/api/me").json()["dev_switch"] is True
    # …including when the identity itself came from the dev headers
    me = client.get("/api/me", headers={"X-Dev-User": "bhavika@example.com",
                                        "X-Dev-Role": "TASpecialist"}).json()
    assert me["user"] == "bhavika@example.com"
    assert me["role"] == "TASpecialist"
    assert me["dev_switch"] is True


def test_specialists_cannot_reach_admin_routes(client):
    for path in ("/api/scoring-model", "/api/jurisdictions", "/api/rbac",
                 "/api/runs", "/api/audit-log"):
        response = client.get(path, headers=_as("TASpecialist"))
        assert response.status_code == 403, path


def test_it_is_off_everywhere(client):
    # 2026-07-19: IT carries zero capabilities until its access is decided —
    # every surface answers 403 (the friendly setup message, since caps are empty).
    for path in ("/api/scoring-model", "/api/runs", "/api/audit-log", "/api/roles"):
        response = client.get(path, headers=_as("IT"))
        assert response.status_code == 403, path
        assert "access isn't configured" in response.json()["detail"]


def test_unassigned_user_gets_friendly_403_everywhere(client):
    headers = {"X-MS-CLIENT-PRINCIPAL": principal("new@example.com", [])}
    response = client.get("/api/roles", headers=headers)
    assert response.status_code == 403
    assert "access isn't configured" in response.json()["detail"]


def test_health_needs_no_identity(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True


def test_health_names_the_build_so_a_deploy_can_be_confirmed(client, monkeypatch):
    """Every other route — and every static asset — is behind auth, so a hosted
    deployment could only be confirmed from the platform's event log, which has
    been ambiguous in practice. The Dockerfile bakes the commit into APP_BUILD."""
    monkeypatch.setenv("APP_BUILD", "033b8ea")
    assert client.get("/api/health").json() == {"ok": True, "build": "033b8ea"}


def test_health_reports_an_empty_build_when_unset(client, monkeypatch):
    monkeypatch.delenv("APP_BUILD", raising=False)
    assert client.get("/api/health").json() == {"ok": True, "build": ""}
