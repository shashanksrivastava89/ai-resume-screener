"""APP_MODE — the three ways into the app (2026-08-20).

The single property worth defending here is FAIL SAFE: a hosted process that was never
told which mode it is in must lock the door, not open it. UAT once served anonymous
requests as SuperAdmin precisely because "unconfigured" meant "dev". These tests pin the
resolution table, the explicit-env-wins back-compat rule the live deployment relies on,
and the fact that rbac.py's enforcement actually follows the mode.
"""

from __future__ import annotations

import pytest

from resume_screener import modes, rbac


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from "nothing configured" — otherwise an ambient APP_MODE or
    REQUIRE_SSO from the developer's shell decides the result instead of the test."""
    for var in ("APP_MODE", "REQUIRE_SSO", "DEV_SWITCH", "LOCAL_SSO",
                "WEBSITE_SITE_NAME", "ENTRA_TENANT_ID", "ENTRA_CLIENT_ID",
                # AZURE_TENANT_ID is the documented fallback for ENTRA_TENANT_ID, so a
                # developer's real .env would otherwise make the "missing id" assertions
                # pass locally and fail in CI (or the reverse).
                "AZURE_TENANT_ID",
                # DEV_USER/DEV_ROLE matter for the same reason and bit harder: dozens of
                # tests in this suite set DEV_ROLE via os.environ and never restore it, so
                # whether this file sees a leaked value depends purely on test ORDERING.
                # That is exactly how "929 passed locally" became a CI failure on
                # `roles == ["SuperAdmin"]` — an earlier file had left
                # "SuperAdmin,TASpecialist" behind. These tests assert what the MODE
                # decides, so they have to own their inputs rather than inherit them.
                "DEV_USER", "DEV_ROLE"):
        monkeypatch.delenv(var, raising=False)


# --- resolution ---------------------------------------------------------------------


def test_local_and_unset_is_dev():
    assert modes.resolve() == "dev"
    assert modes.auth_required() is False
    assert modes.dev_switch_allowed() is True


def test_hosted_and_unset_is_prod_not_dev(monkeypatch):
    """THE regression guard. App Service sets WEBSITE_SITE_NAME. A deployment nobody
    configured must resolve to the STRICTEST mode — forgetting to set APP_MODE should
    cost a locked door, never an open one."""
    monkeypatch.setenv("WEBSITE_SITE_NAME", "resume-screener-uat")
    assert modes.resolve() == "prod"
    assert modes.auth_required() is True
    assert modes.dev_switch_allowed() is False


def test_an_invalid_value_falls_back_safely_rather_than_guessing(monkeypatch):
    """A typo ("APP_MODE=UAT-2", "APP_MODE=production") must not silently become dev."""
    monkeypatch.setenv("WEBSITE_SITE_NAME", "resume-screener-uat")
    monkeypatch.setenv("APP_MODE", "production")
    assert modes.resolve() == "prod"

    monkeypatch.delenv("WEBSITE_SITE_NAME")
    monkeypatch.setenv("APP_MODE", "nonsense")
    assert modes.resolve() == "dev"   # local, so dev is the safe default


@pytest.mark.parametrize("raw,expected", [
    ("dev", "dev"), ("DEV", "dev"), ("  uat  ", "uat"), ("Prod", "prod"),
])
def test_the_value_is_case_and_space_insensitive(monkeypatch, raw, expected):
    monkeypatch.setenv("APP_MODE", raw)
    assert modes.resolve() == expected


# --- what each mode means -----------------------------------------------------------


def test_the_three_modes_differ_in_exactly_the_ways_that_matter(monkeypatch):
    table = {}
    for mode in modes.MODES:
        monkeypatch.setenv("APP_MODE", mode)
        table[mode] = (modes.auth_required(), modes.dev_switch_allowed(),
                       modes.policy().sso)

    assert table == {
        # mode:  (auth required, role switcher, who runs the OIDC flow)
        "dev":  (False, True,  "none"),
        "uat":  (True,  False, "direct"),
        "prod": (True,  False, "easyauth"),
    }


def test_only_dev_ever_hands_out_capabilities_without_a_principal(monkeypatch):
    """The end-to-end version of the same claim, through rbac itself rather than the
    mode table — this is what a route actually consults."""
    monkeypatch.setenv("APP_MODE", "dev")
    user, role, roles = rbac.get_identity_full({})
    # The local persona holds HR Head beside SuperAdmin (2026-10-14): SuperAdmin alone
    # no longer sees AI verdicts, and local dev should still see everything.
    assert roles == ["SuperAdmin", "HRHead"] and user and role == "SuperAdmin"

    for locked in ("uat", "prod"):
        monkeypatch.setenv("APP_MODE", locked)
        assert rbac.get_identity_full({}) == ("", "", []), locked
        assert rbac.capabilities_for(rbac.get_identity_full({})[2]) == set()


# --- back-compat: an explicit flag still wins ---------------------------------------


def test_explicit_require_sso_overrides_the_mode(monkeypatch):
    """The live UAT app sets REQUIRE_SSO=1 and has no APP_MODE. It must keep behaving
    exactly as it does today, so the flag has to win over the mode default."""
    monkeypatch.setenv("APP_MODE", "dev")
    monkeypatch.setenv("REQUIRE_SSO", "1")
    assert modes.auth_required() is True
    assert rbac.get_identity_full({}) == ("", "", [])


def test_explicit_dev_switch_off_overrides_dev_mode(monkeypatch):
    monkeypatch.setenv("APP_MODE", "dev")
    monkeypatch.setenv("DEV_SWITCH", "0")
    assert modes.dev_switch_allowed() is False


def test_an_empty_flag_means_unset_not_false(monkeypatch):
    """`DEV_SWITCH=` (exported but blank) is what a half-filled .env produces. It must
    mean "the mode decides", not "off" — otherwise dev silently loses its identity."""
    monkeypatch.setenv("APP_MODE", "dev")
    monkeypatch.setenv("DEV_SWITCH", "")
    monkeypatch.setenv("REQUIRE_SSO", "  ")
    assert modes.dev_switch_allowed() is True
    assert modes.auth_required() is False


# --- preflight ----------------------------------------------------------------------


def test_preflight_flags_dev_mode_on_a_hosted_app(monkeypatch):
    """The exact misconfiguration that caused the incident, caught by `screener check`."""
    monkeypatch.setenv("APP_MODE", "dev")
    monkeypatch.setenv("WEBSITE_SITE_NAME", "resume-screener-uat")
    failures = [c for c in modes.preflight() if c.ok is False]
    assert any("SuperAdmin" in c.detail for c in failures)


def test_preflight_names_the_missing_entra_ids(monkeypatch):
    monkeypatch.setenv("APP_MODE", "uat")
    by_name = {c.name: c for c in modes.preflight()}
    assert by_name["ENTRA_TENANT_ID"].ok is False
    assert by_name["ENTRA_CLIENT_ID"].ok is False

    monkeypatch.setenv("ENTRA_TENANT_ID", "test-tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "test-client")
    by_name = {c.name: c for c in modes.preflight()}
    assert by_name["ENTRA_TENANT_ID"].ok is True
    assert by_name["ENTRA_CLIENT_ID"].ok is True


def test_preflight_reports_the_admin_consent_gate_as_unknowable(monkeypatch):
    """Admin consent cannot be checked from inside the app, and it is the live blocker
    (ISM-274462). It must show as a CAVEAT (ok=None) rather than a pass or a failure —
    reporting it green would be a lie, and red would be indistinguishable from a
    misconfiguration we could actually fix."""
    monkeypatch.setenv("APP_MODE", "uat")
    consent = next(c for c in modes.preflight() if "consent" in c.name)
    assert consent.ok is None
    assert "ISM-274462" in consent.detail


def test_prod_preflight_objects_to_a_dev_fallback(monkeypatch):
    monkeypatch.setenv("APP_MODE", "prod")
    monkeypatch.setenv("DEV_SWITCH", "1")
    assert any(c.ok is False and "DEV_SWITCH" in c.detail for c in modes.preflight())


def test_describe_is_json_shaped_for_the_ui(monkeypatch):
    monkeypatch.setenv("APP_MODE", "uat")
    payload = modes.describe()
    assert payload["mode"] == "uat"
    assert payload["label"]           # the UI badge needs text
    assert payload["auth_required"] is True
    assert payload["dev_switch"] is False


# --- the sign-in landing + a logout that actually logs you out (2026-08-27) ------------


def _sso_client(monkeypatch):
    """An app with app-driven Entra sign-in installed (APP_MODE=uat)."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("APP_MODE", "uat")
    monkeypatch.setenv("ENTRA_TENANT_ID", "00000000-0000-4000-8000-97d22f865257")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "00000000-0000-4000-8000-8c070c71e599")
    import importlib

    from resume_screener.api import entra_sso

    importlib.reload(entra_sso)
    from fastapi import FastAPI

    app = FastAPI()
    entra_sso.install(app)

    @app.get("/")
    def root():  # stands in for the SPA document route
        return {"ok": True}

    return TestClient(app, follow_redirects=False), entra_sso


def test_unauthenticated_documents_land_on_the_sign_in_page(monkeypatch):
    """NOT straight to Microsoft. An instant bounce left nowhere for "sign out" to
    land — you were signed back in before seeing anything — and no way to choose a
    different account."""
    client, _ = _sso_client(monkeypatch)
    res = client.get("/")
    assert res.status_code in (302, 307)
    assert "/.auth/signin" in res.headers["location"]
    assert "next=" in res.headers["location"]  # and it remembers where you were going


def test_the_sign_in_page_stands_alone(monkeypatch):
    """It renders for someone with no session, so it must not depend on the SPA
    bundle, /api/me or the design tokens the app loads after auth."""
    client, _ = _sso_client(monkeypatch)
    page = client.get("/.auth/signin?next=/reqs")
    assert page.status_code == 200
    body = page.text
    assert "Sign in with Microsoft" in body
    assert "/.auth/login/aad?post_login_redirect_uri=/reqs" in body
    assert "<script" not in body.lower()   # nothing to load, nothing to fail
    assert "{heading}" not in body          # the template actually rendered


def test_logout_lands_signed_out_rather_than_bouncing_back_in(monkeypatch):
    """The bug this closes: logout dropped the session, redirected to "/", and the
    redirect signed the same person straight back in."""
    client, _ = _sso_client(monkeypatch)
    res = client.get("/.auth/logout")
    assert res.status_code in (302, 307)
    assert "signed_out=1" in res.headers["location"]

    page = client.get("/.auth/signin?signed_out=1")
    assert "signed out" in page.text.lower()
    # ...and it still offers the way back in.
    assert "Sign in with Microsoft" in page.text


def test_health_and_the_auth_routes_are_never_redirected(monkeypatch):
    """The container HEALTHCHECK and the login flow itself must stay reachable."""
    client, _ = _sso_client(monkeypatch)
    assert client.get("/.auth/signin").status_code == 200
    # /api/* answers 401 with the header api.ts acts on, never a redirect.
    res = client.get("/api/roles")
    assert res.status_code == 401
    assert res.headers.get("X-Auth-Login") == "/.auth/login/aad"
