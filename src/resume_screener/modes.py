"""The three ways into this app — one switch, resolved in one place (2026-08-20).

WHY THIS EXISTS. There have always been three entry paths, but none of them had a
name. Which one you got was an emergent property of four independent env flags
(`REQUIRE_SSO`, `DEV_SWITCH`, `LOCAL_SSO`, `DEV_ROLE`) that each did one small thing
and could silently contradict each other. That is how UAT ended up serving anonymous
requests as SuperAdmin: nobody set a flag that did not exist to forget. A single
named mode makes the intent declarable, greppable, testable and visible in the UI.

    APP_MODE = dev | uat | prod

| | `dev` | `uat` | `prod` |
|---|---|---|---|
| Who signs in | nobody | Entra, app-driven | Entra, Easy Auth |
| Identity source | `DEV_USER`/`DEV_ROLE` | `X-MS-CLIENT-PRINCIPAL`, synthesized by `entra_sso.py` after it runs the OIDC code flow itself | `X-MS-CLIENT-PRINCIPAL`, injected by App Service in front of the container |
| No identity ⇒ | SuperAdmin (the point) | sign-in redirect / 401 | no identity, **no capabilities** |
| Role switcher | on | off | off |

**All three satisfy ONE identity contract.** `rbac.py` reads exactly one thing — the
`X-MS-CLIENT-PRINCIPAL` header — and `dev` is simply the mode where no such header
exists and the env fills in. `uat` and `prod` differ only in WHO completes the OIDC
dance (this process, or App Service in front of it); the header, the claim types and
every downstream route are byte-identical. That is deliberate: an environment that
authorizes differently from the one you tested is the bug this shape prevents.

FAIL SAFE. With `APP_MODE` unset, a HOSTED process (App Service sets
`WEBSITE_SITE_NAME`) resolves to **`prod`** — the strictest mode — and only a local
process falls to `dev`. An unrecognised value does the same rather than guessing. So
forgetting to configure a deployment now costs you a locked door, never an open one.

BACK-COMPAT. An explicitly set `REQUIRE_SSO` / `DEV_SWITCH` / `LOCAL_SSO` still wins
over the mode's default, so the live UAT app keeps behaving exactly as it does today
until someone sets `APP_MODE`. `rbac.py` consults the mode only where those flags are
absent.

NO IDENTITY VALUES IN SOURCE. The tenant and client id are read from the environment
(`ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`) and this file contains neither them nor a
placeholder for them. They are not secrets — they are public identifiers — but Acme's
ADC deployment validator rejects source containing an Azure client id, tenant id or any
GUID near an identity key, and placeholders (`YOUR_CLIENT_ID`, `xxxxxxxx-…`) fail the
same check while also failing at runtime in a more confusing way. Hosted, they arrive
as App Service app settings; locally, from your gitignored `.env`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

Mode = Literal["dev", "uat", "prod"]
MODES: tuple[Mode, ...] = ("dev", "uat", "prod")

# Which party completes the OIDC flow. "none" = nobody, identity comes from env.
SsoKind = Literal["none", "direct", "easyauth"]


@dataclass(frozen=True)
class ModePolicy:
    """What a mode means, everywhere. Frozen so no caller can mutate the contract."""

    mode: Mode
    label: str            # UI badge text; empty in prod (no badge on the real thing)
    auth_required: bool   # no valid principal => no identity and no capabilities
    dev_switch: bool      # DEV_USER/DEV_ROLE fallback + the X-Dev-User/Role headers
    sso: SsoKind
    summary: str          # one plain line, shown by `screener check` and /api/me


POLICIES: dict[Mode, ModePolicy] = {
    "dev": ModePolicy(
        mode="dev",
        label="Dev — no sign-in",
        auth_required=False,
        dev_switch=True,
        sso="none",
        summary="No sign-in. Identity comes from DEV_USER/DEV_ROLE and the role "
                "switcher is live, so every role is reachable without Entra.",
    ),
    "uat": ModePolicy(
        mode="uat",
        label="UAT — Entra sign-in",
        auth_required=True,
        dev_switch=False,
        sso="direct",
        summary="Real Entra sign-in, run by the app itself against the "
                "Resume-Screener-Dev registration. No role switcher; capabilities "
                "come from Entra app roles exactly as they will in production.",
    ),
    "prod": ModePolicy(
        mode="prod",
        label="",
        auth_required=True,
        dev_switch=False,
        sso="easyauth",
        summary="Real Entra sign-in via App Service Easy Auth in front of the "
                "container. The app never sees a token — only the principal header.",
    ),
}


def hosted() -> bool:
    """True on Azure App Service, which sets WEBSITE_SITE_NAME for every process."""
    return bool(os.environ.get("WEBSITE_SITE_NAME"))


def resolve() -> Mode:
    """The active mode. Fail safe: hosted and unset/invalid => prod, never dev."""
    fallback: Mode = "prod" if hosted() else "dev"
    raw = (os.environ.get("APP_MODE") or "").strip().lower()
    if not raw:
        return fallback
    if raw in MODES:
        return raw  # type: ignore[return-value]
    log.error(
        "APP_MODE=%r is not one of %s — falling back to the safe default %r.",
        raw, ", ".join(MODES), fallback,
    )
    return fallback


def policy() -> ModePolicy:
    """The active mode's policy object."""
    return POLICIES[resolve()]


def _env_flag(name: str) -> bool | None:
    """A tri-state env flag: True / False / None when unset. None means 'the mode
    decides' — an explicitly set flag always wins, which is what keeps the existing
    deployments byte-identical until APP_MODE is set on them."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def auth_required() -> bool:
    """Whether a request with no valid principal gets zero capabilities.
    Explicit REQUIRE_SSO wins; otherwise the mode decides."""
    explicit = _env_flag("REQUIRE_SSO")
    return policy().auth_required if explicit is None else explicit


def dev_switch_allowed() -> bool:
    """Whether the DEV_USER/DEV_ROLE fallback and the X-Dev-User/Role headers apply.
    Explicit DEV_SWITCH wins; otherwise the mode decides."""
    explicit = _env_flag("DEV_SWITCH")
    return policy().dev_switch if explicit is None else explicit


def direct_sso_enabled() -> bool:
    """Whether entra_sso.py should run the OIDC flow in-process.
    Explicit LOCAL_SSO wins (its original localhost meaning); otherwise the mode does."""
    explicit = _env_flag("LOCAL_SSO")
    return policy().sso == "direct" if explicit is None else explicit


# --- Entra identifiers (never hardcoded — see the module docstring) -----------------

def entra_tenant_id() -> str:
    return (os.environ.get("ENTRA_TENANT_ID") or os.environ.get("AZURE_TENANT_ID") or "").strip()


def entra_client_id() -> str:
    return (os.environ.get("ENTRA_CLIENT_ID") or "").strip()


# --- Preflight ---------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    """One prerequisite for the active mode. `ok=None` = cannot be checked from here
    (a human or a ticket has to confirm it) — reported as a caveat, not a failure."""

    name: str
    ok: bool | None
    detail: str


def preflight(mode: Mode | None = None) -> list[Check]:
    """What the given mode needs, and whether it is satisfied in this process.

    Deliberately read-only and network-free: it answers "is this environment
    configured for the mode it claims to be in?" so a misconfiguration surfaces at
    `screener check` time rather than as a mystery 401 or, worse, an open door."""
    active = mode or resolve()
    pol = POLICIES[active]
    checks: list[Check] = [
        Check("mode", True, f"{active} — {pol.summary}"),
    ]

    if active == "dev":
        checks.append(Check(
            "no sign-in expected", True,
            f"DEV_USER={os.environ.get('DEV_USER') or '(default)'} · "
            f"DEV_ROLE={os.environ.get('DEV_ROLE') or '(default SuperAdmin)'}",
        ))
        if hosted():
            checks.append(Check(
                "not hosted", False,
                "APP_MODE=dev on App Service would serve anonymous requests as "
                "SuperAdmin. Use uat or prod for anything with a URL.",
            ))
        return checks

    # uat + prod both need a real registration behind them.
    tenant, client = entra_tenant_id(), entra_client_id()
    checks.append(Check(
        "ENTRA_TENANT_ID", bool(tenant),
        "set" if tenant else "missing — sign-in cannot start (see .env.example)",
    ))

    if active == "uat":
        checks += [
            Check("ENTRA_CLIENT_ID", bool(client),
                  "set" if client else "missing — sign-in cannot start"),
            Check("client secret in Key Vault", None,
                  "easyauth-client-secret in ReesumeScreenerKV, read at runtime via "
                  "DefaultAzureCredential. Not checked here (it needs a network call)."),
            Check("Entra admin consent", None,
                  "MANUAL GATE — tracked on ISM-274462. Until an admin grants consent "
                  "for openid/profile/email, every first sign-in stops at 'Need admin "
                  "approval' and no code change can get past it."),
            Check("app-role assignments", None,
                  "Each tester needs an app role on the enterprise app, or they sign "
                  "in fine and land on 'access not configured'."),
        ]
        return checks

    checks += [
        Check("Easy Auth in front", None,
              "App Service authentication must be configured AND enforced "
              "(requireAuthentication: true). The app cannot verify this from inside."),
        Check("no dev fallback", not dev_switch_allowed(),
              "off" if not dev_switch_allowed()
              else "DEV_SWITCH is explicitly on in prod — an unauthenticated request "
                   "would be served as DEV_ROLE. Unset it."),
    ]
    return checks


def describe() -> dict:
    """The mode as JSON for /api/me — so the UI can badge the environment and never
    has to infer it from the shape of the identity payload."""
    pol = policy()
    return {
        "mode": pol.mode,
        "label": pol.label,
        "summary": pol.summary,
        "auth_required": auth_required(),
        "dev_switch": dev_switch_allowed(),
        "sso": pol.sso,
        "hosted": hosted(),
    }
