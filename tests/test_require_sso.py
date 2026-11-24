"""REQUIRE_SSO=1 — no Easy-Auth principal means no identity (2026-08-14).

`DEV_SWITCH=0` disables only the header switcher; the DEV_USER/DEV_ROLE env fallback
underneath it is unconditional. So a hosted deployment whose Easy Auth is not enforced
serves every anonymous request as SuperAdmin — verified on UAT, where an
unauthenticated /api/me returned all 15 capabilities against a populated database.
These tests pin the defence-in-depth line behind edge enforcement.
"""

from __future__ import annotations

import base64
import json

import pytest

from resume_screener import rbac


def _principal(email: str, roles: list[str]) -> str:
    claims = [{"typ": "preferred_username", "val": email}]
    claims += [{"typ": "roles", "val": r} for r in roles]
    return base64.b64encode(json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("REQUIRE_SSO", "DEV_USER", "DEV_ROLE", "DEV_SWITCH"):
        monkeypatch.delenv(var, raising=False)


def test_off_by_default_the_dev_fallback_still_works(monkeypatch):
    """Local dev and the whole existing suite depend on this."""
    monkeypatch.setenv("DEV_USER", "arjun.test@example.com")
    monkeypatch.setenv("DEV_ROLE", "TASpecialist")
    user, role, roles = rbac.get_identity_full({})
    assert user == "arjun.test@example.com"
    assert roles == ["TASpecialist"]
    assert role == "TASpecialist"


def test_on_a_headerless_request_gets_nothing(monkeypatch):
    """The exact UAT hole: anonymous must NOT inherit DEV_USER's SuperAdmin."""
    monkeypatch.setenv("REQUIRE_SSO", "1")
    monkeypatch.setenv("DEV_USER", "shashank_srivastava@example.com")
    monkeypatch.setenv("DEV_ROLE", "SuperAdmin")
    user, role, roles = rbac.get_identity_full({})
    assert roles == []
    assert user == ""
    assert rbac.capabilities_for(roles) == set()


def test_on_the_dev_switch_headers_are_also_ignored(monkeypatch):
    """A client must not be able to assert an identity when SSO is required."""
    monkeypatch.setenv("REQUIRE_SSO", "1")
    monkeypatch.setenv("DEV_SWITCH", "1")  # switcher "enabled" — still must not apply
    _u, _r, roles = rbac.get_identity_full(
        {"x-dev-user": "attacker@example.com", "x-dev-role": "SuperAdmin"}
    )
    assert roles == []


def test_on_a_real_principal_still_works(monkeypatch):
    """Enforcement must not break the path it exists to protect."""
    monkeypatch.setenv("REQUIRE_SSO", "1")
    header = _principal("u120729@example.com", ["SuperAdmin"])
    user, role, roles = rbac.get_identity_full({rbac.PRINCIPAL_HEADER: header})
    assert user == "u120729@example.com"
    assert roles == ["SuperAdmin"]
    assert "recruiter" in rbac.capabilities_for(roles)


def test_on_an_authenticated_user_with_no_app_role_gets_nothing(monkeypatch):
    """Signed in but unassigned — the 'access not configured' page. Distinct from
    anonymous, and it must NOT fall through to the dev fallback either."""
    monkeypatch.setenv("REQUIRE_SSO", "1")
    monkeypatch.setenv("DEV_ROLE", "SuperAdmin")
    header = _principal("newjoiner@example.com", [])
    user, _role, roles = rbac.get_identity_full({rbac.PRINCIPAL_HEADER: header})
    assert user == "newjoiner@example.com"
    assert roles == []
    assert rbac.capabilities_for(roles) == set()


def test_on_an_unknown_role_is_dropped_not_trusted(monkeypatch):
    """The SuperAdmin-value-typo failure mode: an unrecognised role yields zero
    capabilities rather than anything permissive."""
    monkeypatch.setenv("REQUIRE_SSO", "1")
    header = _principal("someone@example.com", ["Users/Groups"])
    _u, _r, roles = rbac.get_identity_full({rbac.PRINCIPAL_HEADER: header})
    assert roles == []
