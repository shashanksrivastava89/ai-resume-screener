"""Role-based access control, SSO-backed (P25 hooks, P27 enforcement, P36 union).

Seven Entra app roles map to in-app capabilities via rbac.yaml. A principal may
hold SEVERAL app roles at once (P36) — the effective capability set is the UNION
across every known role held, so e.g. a TASpecialist who is also a SuperAdmin can
both screen and edit feature flags. A single "display role" (the most-capable one)
is still derived for the UI badge.

Identity resolution order (get_identity_full):

1. Easy Auth (hosted): Azure Container Apps authentication injects
   `X-MS-CLIENT-PRINCIPAL` (base64 JSON of the token claims) on every request —
   the app consumes it, no auth SDK needed. Email + app roles come from the
   claims; an authenticated user with NO known app role gets an empty role list
   (=> zero capabilities => the "access not configured" page).
2. Local dev: DEV_USER / DEV_ROLE env vars (defaults arjun.test@example.com /
   SuperAdmin). DEV_ROLE accepts a comma list ("TASpecialist,SuperAdmin") so
   multi-role unions stay testable without SSO.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
from typing import Iterable, Mapping

import yaml
from resume_screener.pathkey import canon

# Display precedence: most-capable first. known_roles() and display_role() both
# rely on this ordering, so the UI badge always shows the strongest role held.
# SuperAdmin leads (it is the superset since 2026-07-19); Legal follows the
# HR Head as a named config reviewer/approver.
ROLES = ["SuperAdmin", "HRHead", "Legal", "IT", "TASpecialist", "HRSpecialist", "HiringManager"]

# Capabilities used across the app.
RECRUITER = "recruiter"
REQS_ALL = "reqs_all"          # every requisition, not just own/assigned (2026-07-19)
REQS_READ = "reqs_read"        # READ-ONLY requisitions + workspace (Legal/audit, 2026-07-21)
RULES_VIEW = "rules_view"
RULES_EDIT = "rules_edit"
SCORING_EDIT = "scoring_edit"      # propose scoring-model changes (knobs + signals + topics)
CHANGES_APPROVE = "changes_approve"  # approve/decline change proposals — never one's own (P51)
RUNS_AUDIT = "runs_audit"
DATA_MANAGE = "data_manage"
PREFS_OWN = "prefs_own"        # manage one's own screening preferences (P39)
PREFS_APPROVE = "prefs_approve"  # HR Head approval queue for flagged prefs (P39)
ACCESS_VIEW = "access_view"    # Governance -> Access & Roles (SuperAdmin only, 2026-07-19)
ACCESS_EDIT = "access_edit"    # grant/remove a person's regions (SuperAdmin, 2026-09-12)
FLAGS_VIEW = "flags_view"      # see the feature-flags / Governance surface (P37)
FLAGS_EDIT = "flags_edit"      # toggle feature flags (P37)
HM_TAKE = "hm_take"            # hiring-manager portal: record a "take" (P41)
# See the AI's verdict on a candidate — band, score, summary, score math, evidence,
# strengths/watch-outs, gate results, batch quality and the ranking ORDER. Since
# 2026-10-21 (owner's decision) every role that can open a requisition's candidates
# holds it; it stays a capability so a deployment can narrow it again in rbac.yaml,
# and api/verdict.py still takes it out of a payload for anyone without it.
VERDICT_VIEW = "verdict_view"
# See the hiring DECISIONS (Move forward / Discuss / Not a fit, the note, the
# hiring manager's take) on EVERY requisition one can open — HR Head (2026-10-21).
# Everyone else sees them only on a requisition that is theirs: the owner, its
# hiring manager, a collaborator (app._sees_decisions; api/decision_view.py).
DECISIONS_ALL = "decisions_all"
# The capabilities that open a requisition's candidates at all — without one,
# verdict_view and decisions_all are dropped (effective_capabilities).
_OPENS_CANDIDATES = frozenset({RECRUITER, HM_TAKE, REQS_READ})
# Any one of these => Admin mode is visible. flags_view is here so a pure
# SuperAdmin (feature flags only) still reaches Governance.
ADMIN_CAPS = (RULES_VIEW, RUNS_AUDIT, DATA_MANAGE, FLAGS_VIEW)

# Fallback matrix if rbac.yaml is missing/unreadable — matches the shipped file.
_DEFAULT_MATRIX: dict[str, list[str]] = {
    # Every edit and every requisition, and the AI's read on every candidate — but
    # not the team's DECISIONS, except on a requisition of their own (2026-10-21).
    "SuperAdmin": [RECRUITER, REQS_ALL, REQS_READ, RULES_VIEW, RULES_EDIT, SCORING_EDIT,
                   CHANGES_APPROVE, RUNS_AUDIT, DATA_MANAGE, PREFS_OWN,
                   PREFS_APPROVE, ACCESS_VIEW, ACCESS_EDIT, FLAGS_VIEW, FLAGS_EDIT,
                   HM_TAKE, VERDICT_VIEW],
    # All reqs + approvals; NO Access & Roles, NO feature flags, NO Data page
    # (2026-07-19 — destructive deletion stays with the Super Admin).
    "HRHead": [RECRUITER, REQS_ALL, RULES_VIEW, RULES_EDIT, SCORING_EDIT,
               CHANGES_APPROVE, RUNS_AUDIT, PREFS_OWN, PREFS_APPROVE, VERDICT_VIEW,
               DECISIONS_ALL],
    # Legal reviews the config AND, driven by the audit trail (candidate-name
    # search), needs to see requisitions + the whole workspace READ-ONLY
    # (reqs_read; every write stays recruiter-gated, so they can look, not touch).
    "Legal": [RULES_VIEW, RULES_EDIT, SCORING_EDIT, CHANGES_APPROVE, RUNS_AUDIT, REQS_READ,
              VERDICT_VIEW],
    "IT": [],   # turned off for now (2026-07-19) — access to be decided
    "TASpecialist": [RECRUITER, PREFS_OWN, VERDICT_VIEW],
    "HRSpecialist": [RECRUITER, PREFS_OWN, VERDICT_VIEW],
    # NO recruiter (no create/screen/decide) and NO prefs_own (2026-07-19):
    # preferences apply to the runs of the person who SCREENS — a hiring manager
    # never screens, so the preferences surface would only mislead them. They see
    # the AI's read on the candidates SHARED with them, and nothing pool-wide.
    "HiringManager": [HM_TAKE, VERDICT_VIEW],
}

DEFAULT_ROLE = "SuperAdmin"   # the local-dev badge until SSO supplies the real role
# The local-dev persona's roles. The persona also holds HR Head, so local dev sees
# the decisions on every requisition, not only its own (2026-10-21).
# Hosted deployments never reach this: they require an SSO principal.
DEFAULT_ROLES = ("SuperAdmin", "HRHead")
DEFAULT_USER = "arjun.test@example.com"   # the Arjun · Admin demo persona


# resolved path -> (size, mtime_ns, matrix). The capability check runs on every
# request, so this file was read and parsed on every request too (2026-09-10).
# Keyed on the RESOLVED path: "rbac.yaml" is relative, and a seeded copy in another
# root can carry the same size and mtime as the file it came from.
_RBAC_CACHE: dict[str, tuple[int, int, dict[str, list[str]]]] = {}


def _default_matrix() -> dict[str, list[str]]:
    return {r: list(c) for r, c in _DEFAULT_MATRIX.items()}


def _parse_rbac(p: Path) -> dict[str, list[str]]:
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        roles = data.get("roles") or {}
        matrix = {role: list(caps or []) for role, caps in roles.items()}
        withheld = {str(c) for c in (data.get("withheld") or [])}
    except (OSError, yaml.YAMLError, AttributeError):
        return _default_matrix()
    return with_new_capabilities(matrix, withheld)


def with_new_capabilities(matrix: dict[str, list[str]],
                          withheld: set[str] | frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """The deployment's matrix, plus the build's grants for every capability the
    deployment's file has never mentioned.

    DATA_ROOT copies rbac.yaml onto the share ONCE and never overwrites it, so a
    capability added by a later release reached a fresh deployment and no other:
    UAT's Super Admins went without `access_edit` from 2026-09-12 because their
    file predates it, and `verdict_view` would have left nobody on UAT able to see
    a score. A capability the file DOES mention — granted to anyone — is the
    deployment's own decision and is left exactly as written. To switch a new
    capability off everywhere, name it under `withheld:` in the file."""
    mentioned = {cap for caps in matrix.values() for cap in caps} | set(withheld)
    out = {role: list(caps) for role, caps in matrix.items()}
    for role, caps in _DEFAULT_MATRIX.items():
        for cap in caps:
            if cap in mentioned:
                continue
            granted = out.setdefault(role, [])
            if cap not in granted:
                granted.append(cap)
    return out


def load_rbac(path: str | Path = "rbac.yaml") -> dict[str, list[str]]:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return _default_matrix()
    try:
        key = canon(p)
    except OSError:
        key = str(p)
    hit = _RBAC_CACHE.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    matrix = _parse_rbac(p)
    _RBAC_CACHE[key] = (st.st_size, st.st_mtime_ns, matrix)
    return matrix


def invalidate_rbac() -> None:
    """Drop the cached matrix. Called by the access editor's publish."""
    _RBAC_CACHE.clear()


# Easy Auth's injected headers (case-insensitive; Starlette lowercases them).
PRINCIPAL_HEADER = "x-ms-client-principal"
PRINCIPAL_NAME_HEADER = "x-ms-client-principal-name"

# Dev user switching (until SSO/Entra roles are configured): the client may send
# these headers to act as a different user/role WITHOUT an app restart — demos
# switch users live, and an in-flight screening job stays visible because the
# process (and its in-memory job store) never restarts. Honored ONLY when no
# Easy-Auth principal is present (hosted requests always carry one — Easy Auth
# strips client-supplied copies) — same trust level as the DEV_USER/DEV_ROLE env
# vars they override. DEV_SWITCH=0 disables them (kill switch for a deployment
# that somehow runs without Easy Auth).
DEV_USER_HEADER = "x-dev-user"
DEV_ROLE_HEADER = "x-dev-role"


def _dev_switch_enabled() -> bool:
    """Whether the dev identity path applies at all. Delegated to `modes` so the
    answer is the same one `screener check` and the UI badge report: an explicitly
    set DEV_SWITCH wins, otherwise APP_MODE decides (on in dev, off in uat/prod)."""
    from . import modes

    return modes.dev_switch_allowed()


def _require_sso() -> bool:
    """A request with no valid Easy-Auth principal gets NO identity and NO
    capabilities, instead of falling back to DEV_USER/DEV_ROLE.

    Why this exists (2026-08-14): `DEV_SWITCH=0` only disables the *header* switcher.
    The env fallback below it was unconditional, so a hosted deployment whose Easy Auth
    is misconfigured, disabled, or simply not enforced yet served every anonymous
    request as `DEFAULT_ROLE` — SuperAdmin. That is precisely what happened on UAT: an
    unauthenticated `/api/me` returned all 15 capabilities against a populated
    database. Enforcement at the edge is the primary control; this is the
    defence-in-depth line behind it, so a single misconfiguration is not total.

    Since 2026-08-20 this is a property of APP_MODE (`modes.auth_required`) rather than
    a flag someone has to remember: `uat` and `prod` both require an identity, and a
    HOSTED process with APP_MODE unset resolves to `prod`. An explicit REQUIRE_SSO
    still wins, so existing deployments are unaffected."""
    from . import modes

    return modes.auth_required()


def dev_switch_active(headers: Mapping[str, str] | None = None) -> bool:
    """True when this request resolves identity through the dev path (no SSO
    principal) and switching isn't disabled — the client shows the switcher."""
    if not _dev_switch_enabled():
        return False
    return headers is None or not headers.get(PRINCIPAL_HEADER)

# Claim types that carry the signed-in user's email/UPN, most-preferred first.
_EMAIL_CLAIMS = (
    "preferred_username",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "email",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
    "upn",
)
# App roles arrive as one claim per role under either of these types.
_ROLE_CLAIMS = ("roles", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")
# The country the directory holds for this person, when the app registration is
# configured to emit it as an optional claim (2026-09-12). Used ONLY to give their
# region lens a sensible starting point — never to decide access, and absent by
# default, in which case nothing changes.
_COUNTRY_CLAIMS = (
    "ctry",
    "country",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/country",
)


def parse_client_principal(header_value: str) -> dict | None:
    """Decode the X-MS-CLIENT-PRINCIPAL header -> {email, name, roles, country}.

    The header is base64(JSON) with a `claims` list of {typ, val}. Returns None
    for anything undecodable — a malformed header must degrade to "no identity",
    never to a traceback (guardrail §6.1 thinking: headers are input, not trust)."""
    try:
        decoded = base64.b64decode(header_value, validate=True)
        payload = json.loads(decoded)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    claims = payload.get("claims") or []
    if not isinstance(claims, list):
        return None
    by_type: dict[str, list[str]] = {}
    for claim in claims:
        if isinstance(claim, dict) and claim.get("typ") and claim.get("val") is not None:
            by_type.setdefault(str(claim["typ"]).lower(), []).append(str(claim["val"]))
    email = next((by_type[t][0] for t in _EMAIL_CLAIMS if by_type.get(t)), "")
    roles = [r for t in _ROLE_CLAIMS for r in by_type.get(t, [])]
    name = (by_type.get("name") or [""])[0]
    country = next((by_type[t][0] for t in _COUNTRY_CLAIMS if by_type.get(t)), "")
    return {"email": email, "name": name, "roles": roles, "country": country}


def principal_country(headers: Mapping[str, str] | None = None) -> str:
    """The signed-in person's country from the token, or "".

    Optional by design: `ctry` is an optional claim on the app registration, and
    the only thing that depends on it is where a region picker opens."""
    raw = (headers or {}).get(PRINCIPAL_HEADER) if headers else None
    if not raw:
        return ""
    parsed = parse_client_principal(raw)
    return str((parsed or {}).get("country") or "").strip()


def principal_display_name(headers: Mapping[str, str] | None = None) -> str:
    """The signed-in person's real name from the token's `name` claim, or "".

    Easy Auth already delivers this and it was being discarded, so the UI showed a
    UPN — `u115652@example.com` rather than "Jasvinder Cremin". Acme UPNs are
    payroll-style ids, so deriving a name from the address (names.display_name) yields
    "U115652"; only the claim carries the real thing.

    NOT the job title: `jobTitle` is a directory attribute, not a token claim, so it
    would need a claims-mapping policy or a Graph call with User.Read.All and admin
    consent. Deliberately left out rather than putting the sidebar behind a consent
    ticket — see the notes on this function's call site in /api/me."""
    if headers is None:
        return ""
    principal = headers.get(PRINCIPAL_HEADER)
    if not principal:
        return ""
    parsed = parse_client_principal(principal)
    return (parsed or {}).get("name", "") or ""


def known_roles(app_roles: Iterable[str]) -> list[str]:
    """The KNOWN roles the principal holds, in ROLES (display-precedence) order.
    Unknown roles are dropped; the result may be empty (=> zero capabilities =>
    the "access not configured" page)."""
    held = set(app_roles)
    return [r for r in ROLES if r in held]


def display_role(roles: Iterable[str]) -> str:
    """The single role shown in the UI badge: the most-capable known role held
    (ROLES is display-ordered), or "" if none."""
    ordered = known_roles(roles)
    return ordered[0] if ordered else ""


def role_from_app_roles(app_roles: list[str]) -> str:
    """Back-compat alias: the most-capable KNOWN single role (the display role)."""
    return display_role(app_roles)


def has_principal(headers: Mapping[str, str] | None = None) -> bool:
    """Whether this request carries a parseable SSO principal at all.

    The question "is anybody signed in?" is distinct from "who are they and what
    may they do?": a request with NO principal is an expired or absent session and
    wants the sign-in page, whereas a principal holding no app role is a real person
    who needs an administrator (get_identity_full returns ("", "", []) for BOTH, and
    conflating them shows "you do not have access" to someone whose token merely
    timed out)."""
    if headers is None:
        return False
    principal = headers.get(PRINCIPAL_HEADER)
    if not principal:
        return False
    return parse_client_principal(principal) is not None


def get_identity_full(
    headers: Mapping[str, str] | None = None,
) -> tuple[str, str, list[str]]:
    """(user, display_role, roles). `roles` = ALL known app roles the principal
    holds; capabilities are their union (capabilities_for). display_role is the
    most-capable one, for the UI badge. Hosted: the Easy-Auth principal header
    (P27). Local dev / CLI: DEV_USER / DEV_ROLE env — DEV_ROLE accepts a comma
    list ("TASpecialist,SuperAdmin"). `headers` is any case-insensitive mapping
    (FastAPI's request.headers); None means a headerless context (CLI)."""
    principal = headers.get(PRINCIPAL_HEADER) if headers is not None else None
    if principal:
        parsed = parse_client_principal(principal)
        if parsed is not None:
            user = parsed["email"] or (headers.get(PRINCIPAL_NAME_HEADER) or "").strip()
            roles = known_roles(parsed["roles"])
            return user or DEFAULT_USER, display_role(roles), roles
    # SSO-only deployments stop here: no principal => no identity, no roles, no
    # capabilities => the "access not configured" page. Never the dev fallback.
    if _require_sso():
        return "", "", []
    # Dev path: the switcher headers (if allowed) override the env, each
    # independently, through the exact same parsing as the env values.
    hdr_user = hdr_role = None
    if headers is not None and _dev_switch_enabled():
        hdr_user = (headers.get(DEV_USER_HEADER) or "").strip() or None
        hdr_role = (headers.get(DEV_ROLE_HEADER) or "").strip() or None
    user = hdr_user or (os.environ.get("DEV_USER") or DEFAULT_USER).strip()
    dev_role = hdr_role or os.environ.get("DEV_ROLE")
    raw = [r.strip() for r in dev_role.split(",")] if dev_role else list(DEFAULT_ROLES)
    roles = known_roles(raw)
    return user, display_role(roles), roles


def get_identity(headers: Mapping[str, str] | None = None) -> tuple[str, str]:
    """(user, display_role). Thin wrapper over get_identity_full for callers that
    only need the badge role (audit reviewer, single-role tests)."""
    user, role, _roles = get_identity_full(headers)
    return user, role


def get_current_user(headers: Mapping[str, str] | None = None) -> str:
    """Reviewer identity for Decision.reviewer / audit entries (P26.4): the SSO
    email when the auth headers exist, else DEV_USER, else "local-user"."""
    return get_identity(headers)[0]


def capabilities(role: str, rbac: dict[str, list[str]] | None = None) -> set[str]:
    """Capabilities of a SINGLE role (matrix rendering, single-role tests)."""
    return set((rbac if rbac is not None else load_rbac()).get(role, []))


def capabilities_for(
    roles: Iterable[str], rbac: dict[str, list[str]] | None = None
) -> set[str]:
    """The UNION of capabilities across every role held (P36) — the raw matrix grant
    before feature-flag suppression. Use effective_capabilities() for the runtime set."""
    matrix = rbac if rbac is not None else load_rbac()
    caps: set[str] = set()
    for role in roles:
        caps |= set(matrix.get(role, []))
    return caps


# Capabilities that a feature flag can dark-launch: when the flag is OFF the
# capability is stripped from the effective set, so every API guard and the FE (which
# derives its UI from /api/me capabilities) follow from one place. hm_take is gated by
# the env-controlled `hm_portal` flag (P37).
_FLAG_GATED_CAPS = {HM_TAKE: "hm_portal"}


def effective_capabilities(
    roles: Iterable[str], rbac: dict[str, list[str]] | None = None
) -> set[str]:
    """The runtime capability set: the matrix union (capabilities_for) MINUS any
    capability whose gating feature flag is off (P37). This is THE one enforcement
    point for flag-based capability suppression — require()/require_any()/`/api/me`
    all consult it."""
    caps = capabilities_for(roles, rbac)
    gated = caps & set(_FLAG_GATED_CAPS)
    if gated:
        from resume_screener import feature_flags

        for cap in gated:
            if not feature_flags.flag_enabled(_FLAG_GATED_CAPS[cap]):
                caps.discard(cap)
    # Seeing candidates' verdicts or decisions means nothing without a way to open a
    # requisition. A hiring manager whose portal is off keeps NO capability, so they
    # still get the "access not configured" page, not one they cannot use (2026-10-21).
    if not caps & _OPENS_CANDIDATES:
        caps -= {VERDICT_VIEW, DECISIONS_ALL}
    return caps


def can(role: str, capability: str, rbac: dict[str, list[str]] | None = None) -> bool:
    return capability in capabilities(role, rbac)


def has_admin(role: str, rbac: dict[str, list[str]] | None = None) -> bool:
    return has_admin_caps(capabilities(role, rbac))


def may_view_verdict(caps: Iterable[str]) -> bool:
    """May this capability set see the AI's verdict on a candidate? THE one question
    every surface asks (api/verdict.py) — never a second copy of the rule."""
    return VERDICT_VIEW in set(caps)


def sees_all_decisions(caps: Iterable[str]) -> bool:
    """Does this capability set see the hiring decisions on EVERY requisition it can
    open (HR Head)? Half of app._sees_decisions; the other half is ownership."""
    return DECISIONS_ALL in set(caps)


def has_admin_caps(caps: Iterable[str]) -> bool:
    """Admin mode is visible if the (union) capability set holds any ADMIN_CAP."""
    caps = set(caps)
    return any(c in caps for c in ADMIN_CAPS)
