"""App-driven Entra sign-in must be indistinguishable from Easy Auth (2026-08-14).

This module lets an environment with no Easy Auth in front of it — localhost, and UAT
while it is being stood up — do REAL Entra sign-in while `rbac.py` stays untouched.
That only holds if the header it synthesizes parses back through the HOSTED code path
to the same identity. These tests pin that contract, the off-by-default guarantee the
offline suite and CI depend on, and (2026-08-20) the APP_MODE gating that decides when
it runs at all.
"""

from __future__ import annotations

import base64
import json

from resume_screener import rbac
from resume_screener.api import entra_sso


def _entra_claims() -> dict:
    """An ID token payload shaped like the real one for this tenant. Note that
    preferred_username is the UPN, NOT the mail attribute — see below."""
    return {
        "preferred_username": "u120729@example.com",
        "email": "shashank_srivastava@example.com",
        "name": "Shashank Srivastava (Non_Emp)",
        "roles": ["SuperAdmin"],
    }


def test_header_round_trips_through_the_hosted_parser():
    """The whole anti-drift claim in one assertion: what the shim builds is what
    rbac.parse_client_principal (the HOSTED path) reads."""
    header = entra_sso.principal_header(_entra_claims())
    parsed = rbac.parse_client_principal(header)
    assert parsed is not None
    assert parsed["email"] == "u120729@example.com"
    assert parsed["roles"] == ["SuperAdmin"]
    assert parsed["name"] == "Shashank Srivastava (Non_Emp)"


def test_identity_prefers_the_UPN_over_the_mail_attribute():
    """This user's UPN (u120729@) and mail (shashank_srivastava@) DIFFER, and
    rbac._EMAIL_CLAIMS puts preferred_username first — so the app keys identity on
    the UPN. Region grants and decision attribution must target that string; a grant
    against the mail attribute yields a successful login and a totally empty app.
    Pinned because it is invisible until someone hits it in UAT."""
    parsed = rbac.parse_client_principal(entra_sso.principal_header(_entra_claims()))
    assert parsed["email"] != "shashank_srivastava@example.com"
    assert parsed["email"] == "u120729@example.com"


def test_multiple_roles_survive_as_a_union():
    """A principal may hold several app roles; effective capabilities are the UNION."""
    claims = _entra_claims() | {"roles": ["TASpecialist", "HRHead"]}
    parsed = rbac.parse_client_principal(entra_sso.principal_header(claims))
    assert set(parsed["roles"]) == {"TASpecialist", "HRHead"}


def test_header_is_base64_json_in_the_easy_auth_shape():
    """Easy Auth sends base64(JSON) with a `claims` list of {typ, val}. Anything else
    silently yields an anonymous request."""
    decoded = json.loads(base64.b64decode(entra_sso.principal_header(_entra_claims())))
    assert decoded["auth_typ"] == "aad"
    assert all({"typ", "val"} <= set(c) for c in decoded["claims"])
    assert {"typ": "roles", "val": "SuperAdmin"} in decoded["claims"]


def test_disabled_by_default(monkeypatch):
    """Unset LOCAL_SSO must leave the app exactly as CI and the offline suite expect."""
    monkeypatch.delenv("LOCAL_SSO", raising=False)
    assert entra_sso.enabled() is False

    installed: list[str] = []

    class _Spy:
        def get(self, *a, **k):  # noqa: ANN002, ANN003
            installed.append("route")
            return lambda fn: fn

        def middleware(self, *a, **k):  # noqa: ANN002, ANN003
            installed.append("middleware")
            return lambda fn: fn

    entra_sso.install(_Spy())
    assert installed == []


def test_refuses_to_shadow_easy_auth_on_app_service(monkeypatch):
    """Hosted with no APP_MODE set, this must stand down rather than register a second,
    weaker auth path.

    Since 2026-08-20 the mechanism is the fail-safe mode resolution rather than a
    hardcoded `WEBSITE_SITE_NAME` veto: App Service sets that variable, so `resolve()`
    returns `prod`, and `enabled()` refuses in `prod`. Same outcome, and it now also
    covers the case this test could not — a hosted deployment that forgot to configure
    anything at all. `APP_MODE=uat` is the deliberate opt-in that DOES run hosted."""
    monkeypatch.setenv("LOCAL_SSO", "1")
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "resume-screener-uat")
    touched: list[str] = []

    class _Spy:
        def get(self, *a, **k):  # noqa: ANN002, ANN003
            touched.append("route")
            return lambda fn: fn

        def middleware(self, *a, **k):  # noqa: ANN002, ANN003
            touched.append("middleware")
            return lambda fn: fn

    entra_sso.install(_Spy())
    assert touched == []


def test_a_malformed_id_token_does_not_raise():
    """A bad token must yield empty claims (-> a clean 502), never a traceback."""
    assert entra_sso._decode_id_token("not-a-jwt") == {}
    assert entra_sso._decode_id_token("") == {}


# --- the sign-in page you actually see ------------------------------------------------


def _client(monkeypatch, **env):
    """A minimal app with the sign-in routes installed — avoids reloading the real app
    just to flip an import-time env flag.

    The tenant/client ids are supplied as ENV, deliberately not as literals in the
    module under test: they stopped being hardcoded constants on 2026-08-20 (ADC rejects
    source containing an identity GUID). Fake non-GUID values are enough — nothing here
    talks to Entra, it only builds the redirect URL."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LOCAL_SSO", "1")
    monkeypatch.setenv("ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "test-client")
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    app = FastAPI()

    @app.get("/api/health")
    def health():  # noqa: ANN202
        return {"ok": True}

    @app.get("/api/roles")
    def roles():  # noqa: ANN202
        return {"roles": []}

    @app.get("/")
    def index():  # noqa: ANN202
        return {"page": "index"}

    entra_sso.install(app)
    return TestClient(app, follow_redirects=False)


def test_a_browser_request_is_sent_to_the_sign_in_page(monkeypatch):
    """The original gap: with no session the app fell through to DEV_USER, so you never
    saw a sign-in page at all.

    It now stops at OUR landing page rather than bouncing straight to Microsoft
    (2026-08-27). The bounce made signing out impossible — logout dropped the session,
    redirected to "/", and the redirect signed the same person back in before they saw
    anything — and left nobody a way to choose a different account. The Microsoft
    redirect still happens, one deliberate click later."""
    r = _client(monkeypatch).get("/")
    assert r.status_code == 307
    location = r.headers["location"]
    assert location.startswith("/.auth/signin?next=")
    assert "%2F" in location or location.endswith("next=/")  # remembers the destination


def test_the_login_route_redirects_to_entra(monkeypatch):
    r = _client(monkeypatch).get("/.auth/login/aad")
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://login.microsoftonline.com/")
    assert "response_type=code" in loc
    assert "%2F.auth%2Flogin%2Faad%2Fcallback" in loc  # the registered redirect URI


def test_api_calls_get_401_not_a_redirect(monkeypatch):
    """A 302 to Microsoft on an XHR is swallowed by CORS and surfaces as a mystery
    network error; 401 lets the client react."""
    r = _client(monkeypatch).get("/api/roles")
    assert r.status_code == 401


def test_the_401_carries_the_login_hint_header(monkeypatch):
    """`X-Auth-Login` is the ONLY thing api.ts redirects on — a bare 401 must never
    trigger sign-in, or every ordinary authorization failure in the app becomes a
    surprise navigation. It matters on the Vite dev server, where Vite serves
    index.html for "/" itself so the document request is never redirected and the
    SPA's first /api call is the only signal that nobody is signed in."""
    r = _client(monkeypatch).get("/api/roles")
    assert r.headers.get("X-Auth-Login") == "/.auth/login/aad"


def test_the_health_probe_is_never_redirected(monkeypatch):
    """The container HEALTHCHECK has no cookie — redirecting it would mark the app
    unhealthy and cause a restart loop."""
    r = _client(monkeypatch).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# --- APP_MODE gating (2026-08-20) -----------------------------------------------------


def test_prod_refuses_to_shadow_easy_auth_even_if_local_sso_is_set(monkeypatch):
    """In prod, Easy Auth is in front of the container and has already VERIFIED the
    token. Letting this module run there would replace a verified principal with a
    self-run flow — a downgrade dressed as a fallback. enabled() must refuse
    regardless of LOCAL_SSO, so a stray env var cannot weaken production."""
    monkeypatch.setenv("APP_MODE", "prod")
    monkeypatch.setenv("LOCAL_SSO", "1")
    monkeypatch.setenv("ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "test-client")
    assert entra_sso.enabled() is False


def test_uat_turns_sign_in_on_without_any_flag(monkeypatch):
    """The point of APP_MODE: `uat` means "real Entra sign-in" on its own. Nobody
    should have to remember LOCAL_SSO to make a UAT deployment ask for credentials."""
    monkeypatch.setenv("APP_MODE", "uat")
    monkeypatch.delenv("LOCAL_SSO", raising=False)
    assert entra_sso.enabled() is True


def test_missing_ids_refuse_to_install_rather_than_half_working(monkeypatch):
    """With no tenant/client id the login route would redirect to a malformed Entra URL
    and fail as an opaque Microsoft page. Refusing to install leaves the mode's
    auth_required behaviour in charge — a locked door, not an open one."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("APP_MODE", "uat")
    monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)

    app = FastAPI()

    @app.get("/")
    def index():  # noqa: ANN202
        return {"page": "index"}

    entra_sso.install(app)
    # No middleware, no /.auth routes: the request is served, not redirected.
    client = TestClient(app, follow_redirects=False)
    assert client.get("/").json() == {"page": "index"}
    assert client.get("/.auth/login/aad").status_code == 404
