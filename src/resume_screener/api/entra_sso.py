"""App-driven Entra sign-in — the `dev`-adjacent and `uat` half of APP_MODE.

WHY THIS EXISTS. In `prod`, App Service's built-in authentication ("Easy Auth") runs in
front of the container, completes the OIDC dance, and injects a base64 JSON
`X-MS-CLIENT-PRINCIPAL` header carrying the signed-in user's claims. `rbac.py` reads
ONLY that header. Where there is no Easy Auth — localhost, and UAT while it is still
being stood up — the app would otherwise fall back to `DEV_USER`/`DEV_ROLE`: you pick
your own role, which is convenient but is NOT SSO. It never exercises the real claim
shape, the real role values, or the real email the app keys identity on.

THE ANTI-DRIFT PROPERTY. This module performs the authorization-code flow itself and
then synthesizes THE SAME HEADER Easy Auth would have injected. `rbac.py`, the
capability middleware, the audit trail and every route are untouched and cannot tell
the environments apart. That is the whole design: one identity contract, two ways of
satisfying it. Do NOT "improve" this by teaching rbac.py about local sessions — that
would create exactly the drift this exists to prevent.

WHEN IT RUNS. `modes.direct_sso_enabled()` — true in `uat`, and locally whenever
`LOCAL_SSO=1` is set explicitly. In `dev` it is off (the env identity is the point) and
in `prod` it is off (Easy Auth is in front and must not be shadowed). It refuses to run
in `prod` even if something sets `LOCAL_SSO=1` there, because shadowing a verified
principal with a self-run flow would be a downgrade, not a fallback.

SECRETS. The client secret is read from Key Vault at runtime via the same
`DefaultAzureCredential` path as the service API keys (`az login` locally, managed
identity hosted). It is never read from `.env`, never written to disk, never logged,
and never sent to the browser.

NO IDENTITY VALUES IN SOURCE. The tenant and client id come from `ENTRA_TENANT_ID` /
`ENTRA_CLIENT_ID` via `modes`; this file contains neither, and no placeholder for them
either. They used to be module constants, which is both an ADC-validator rejection
(any GUID near an identity key) and a latent bug — the wrong tenant baked into an image
is invisible until someone tries to sign in.

TOKEN VERIFICATION. This decodes the ID token WITHOUT verifying its signature. That is
sound only because the token is fetched by THIS process directly from Entra's token
endpoint over TLS and is never accepted from a client. If that ever changes, the
signature must be verified first.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import urllib.parse
import urllib.request
from typing import Any

# MODULE level on purpose, not inside install(). This module uses
# `from __future__ import annotations`, so every annotation is a lazy string that
# FastAPI resolves with get_type_hints against MODULE GLOBALS. Imported inside
# install(), `Request` is a local of that function, invisible to the resolver — FastAPI
# then treats `request: Request` as an ordinary QUERY PARAMETER and every route 422s
# with {"loc": ["query", "request"], "msg": "Field required"}. Silent and total.
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# Aliased: the route function below is also called `signin_page`, and inside
# register()'s scope that name would resolve to the function, not the module.
from . import signin_page as signin_view

log = logging.getLogger(__name__)

COOKIE = "rs_entra_sso"
_SECRET_NAME = "easyauth-client-secret"
# Exactly what Easy Auth requests at runtime. Keep these three in step with the
# registration's CONFIGURED permissions: admin consent grants what is configured, so a
# scope requested here but not configured there leaves the consent prompt in place.
_SCOPE = "openid profile email"

# The sign-in landing, deliberately a SELF-CONTAINED server-rendered page: it has to
# render for someone who is not signed in, so it cannot depend on the SPA bundle, the
# /api/me payload or the design tokens the app loads after auth. Brand colours are
# inlined for the same reason. Placeholders: heading, blurb, target, note.

# In-process only, deliberately: a server-side session means the browser holds an
# opaque random id and cannot forge roles by editing a cookie. Cost is that a uvicorn
# --reload restart logs you out; that is the right trade for not shipping a
# client-side trust boundary, even in dev.
_SESSIONS: dict[str, dict[str, Any]] = {}
_STATES: dict[str, str] = {}


def enabled() -> bool:
    """Whether this module should run the OIDC flow — see the module docstring.
    `uat` by default, plus an explicit `LOCAL_SSO=1` anywhere that is not `prod`."""
    from .. import modes

    if modes.resolve() == "prod":
        return False
    return modes.direct_sso_enabled()


def _tenant_id() -> str:
    from .. import modes

    return modes.entra_tenant_id()


def _client_id() -> str:
    from .. import modes

    return modes.entra_client_id()


def _client_secret() -> str:
    """The Easy Auth client secret, from Key Vault. Never cached to disk."""
    from ..config import _credential, load_settings

    settings = load_settings()
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=settings.azure_key_vault_uri, credential=_credential())
    return client.get_secret(_SECRET_NAME).value or ""


def _decode_id_token(id_token: str) -> dict[str, Any]:
    """Read the ID token payload. Signature intentionally NOT verified — see module
    docstring: this process obtained the token itself from Entra over TLS."""
    parts = id_token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001 - a malformed token must not 500 the app
        return {}


def principal_header(claims: dict[str, Any]) -> str:
    """Build the base64 JSON `X-MS-CLIENT-PRINCIPAL` value Easy Auth would inject.

    The claim TYPES here are the ones `rbac.py::_EMAIL_CLAIMS` / `_ROLE_CLAIMS`
    already look for, so the parsing path is shared with hosted."""
    flat: list[dict[str, str]] = []

    def add(typ: str, val: Any) -> None:
        if val:
            flat.append({"typ": typ, "val": str(val)})

    # preferred_username is what rbac.py prefers, and hosted Entra populates it with
    # the UPN (u120729@…), NOT the mail attribute (shashank_srivastava@…). Keeping
    # that exact precedence locally is the point — an identity that works here but
    # not hosted (or vice versa) is the drift this module prevents.
    add("preferred_username", claims.get("preferred_username") or claims.get("upn"))
    add("email", claims.get("email"))
    add("name", claims.get("name"))
    # Optional on the app registration; absent unless someone configures it, and
    # nothing breaks when it is (2026-09-12).
    add("ctry", claims.get("ctry"))
    for role in claims.get("roles") or []:
        add("roles", role)

    payload = {
        "auth_typ": "aad",
        "name_typ": "name",
        "role_typ": "roles",
        "claims": flat,
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def install(app: Any) -> None:
    """Register the /.auth routes + the header-injecting middleware.

    MUST be called BEFORE the SPA catch-all route is registered, or `/{full_path:path}`
    swallows /.auth/* and login 404s."""
    from .. import modes

    if not enabled():
        return

    # Being on App Service is no longer a veto — APP_MODE=uat is hosted and this is how
    # it signs people in. `prod` is the veto, and enabled() already refused there, so
    # Easy Auth can never be shadowed by a self-run flow.
    mode = modes.resolve()

    if not (_tenant_id() and _client_id()):
        # Fail LOUD and OPEN-MOUTHED rather than half-installing: with no ids the login
        # route would redirect to a malformed Entra URL and the error would surface as an
        # opaque Microsoft page. Refusing to install leaves the mode's own auth_required
        # behaviour in charge, which is a locked door, not an open one.
        log.error(
            "APP_MODE=%s wants app-driven Entra sign-in but ENTRA_TENANT_ID / "
            "ENTRA_CLIENT_ID are not set — sign-in routes NOT installed. "
            "Run `screener check` for the preflight.",
            mode,
        )
        return

    log.warning(
        "App-driven Entra sign-in is ON (APP_MODE=%s). Identity comes from real Entra "
        "claims; DEV_USER/DEV_ROLE do not apply.", mode,
    )

    def _redirect_uri(request: Request) -> str:
        # Must match a registered redirect URI EXACTLY, and it is derived from the
        # REQUEST rather than configured, so the same image works on localhost:8000
        # (screener ui), localhost:5173 (vite dev) and the hosted UAT origin. Each one
        # still has to be registered on the app registration or Entra rejects it.
        return f"{request.base_url.scheme}://{request.base_url.netloc}/.auth/login/aad/callback"

    @app.get("/.auth/signin")
    def signin_page(request: Request, next: str = "/", signed_out: int = 0):
        """The sign-in landing.

        Unauthenticated document requests used to redirect STRAIGHT to Microsoft, which
        made signing out impossible: /.auth/logout dropped the session, redirected to
        "/", and the bounce signed the same person back in before they saw anything.
        A landing page gives the redirect somewhere to stop, and gives a person who
        needs a different account a place to choose one."""
        return HTMLResponse(signin_view.render(next_url=next, signed_out=bool(signed_out)))

    @app.get("/.auth/login/aad")
    def login(request: Request, post_login_redirect_uri: str = "/"):
        state = secrets.token_urlsafe(24)
        _STATES[state] = post_login_redirect_uri
        query = urllib.parse.urlencode({
            "client_id": _client_id(),
            "response_type": "code",
            "redirect_uri": _redirect_uri(request),
            "response_mode": "query",
            "scope": _SCOPE,
            "state": state,
        })
        return RedirectResponse(
            f"https://login.microsoftonline.com/{_tenant_id()}/oauth2/v2.0/authorize?{query}"
        )

    @app.get("/.auth/login/aad/callback")
    def callback(request: Request, code: str = "", state: str = "", error_description: str = ""):
        if error_description:
            # Surface Entra's own message: AADSTS50105 here means "signed in fine, but
            # you hold no app role" — the single most likely first-run failure.
            return JSONResponse({"error": error_description}, status_code=400)
        target = _STATES.pop(state, None)
        if target is None:
            return JSONResponse({"error": "unknown or expired state; start at /.auth/login/aad"},
                                status_code=400)
        body = urllib.parse.urlencode({
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(request),
            "scope": _SCOPE,
        }).encode()
        req = urllib.request.Request(
            f"https://login.microsoftonline.com/{_tenant_id()}/oauth2/v2.0/token",
            data=body, headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            # The URL is the fixed https Entra token endpoint built above.
            with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
                tokens = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            # The error's type and HTTP status only: the exchange carries the client
            # secret and the code, and nothing of either belongs in a log.
            status = getattr(exc, "code", None)
            log.error("SSO sign-in exchange with Entra failed (%s%s)", type(exc).__name__,
                      f", HTTP {status}" if status else "")
            return JSONResponse({"error": "token exchange failed; see server log"}, status_code=502)

        claims = _decode_id_token(tokens.get("id_token", ""))
        if not claims:
            return JSONResponse({"error": "no id_token in response"}, status_code=502)

        sid = secrets.token_urlsafe(32)
        _SESSIONS[sid] = claims
        response = RedirectResponse(target or "/")
        # http-only; no `secure` because localhost is plain http by design here.
        response.set_cookie(COOKIE, sid, httponly=True, samesite="lax", path="/")
        return response

    @app.get("/.auth/logout")
    def logout(request: Request):
        sid = request.cookies.get(COOKIE)
        if sid:
            _SESSIONS.pop(sid, None)
        # "/" would redirect to the landing which is fine, but say it explicitly so the
        # signed-out state is what the person sees rather than a login prompt.
        response = RedirectResponse("/.auth/signin?signed_out=1")
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/.auth/me")
    def me(request: Request):
        """Mirrors Easy Auth's /.auth/me so the shape is familiar when debugging."""
        claims = _SESSIONS.get(request.cookies.get(COOKIE) or "")
        if not claims:
            return JSONResponse([], status_code=200)
        return JSONResponse([{
            "provider_name": "aad",
            "user_id": claims.get("preferred_username"),
            "user_claims": [{"typ": k, "val": str(v)} for k, v in claims.items()],
        }])

    @app.middleware("http")
    async def inject_principal(request: Request, call_next):
        """Signed in -> inject the hosted header. Not signed in -> behave like Easy
        Auth's `RedirectToLoginPage`, so you actually get a Microsoft sign-in page
        instead of silently falling back to DEV_USER (which would defeat the point)."""
        sid = request.cookies.get(COOKIE)
        claims = _SESSIONS.get(sid or "")
        if claims:
            header = principal_header(claims).encode()
            # Rewrite the raw ASGI headers: downstream sees exactly what App Service
            # would have delivered. Drop any inbound copy first — a client must never
            # be able to assert its own principal.
            name = b"x-ms-client-principal"
            raw = [(k, v) for (k, v) in request.scope["headers"] if k.lower() != name]
            raw.append((name, header))
            request.scope["headers"] = raw
            return await call_next(request)

        path = request.url.path
        if path.startswith("/.auth/") or path == "/api/health":
            # The login routes themselves, and the no-auth liveness probe (which the
            # container HEALTHCHECK hits) must never be redirected.
            return await call_next(request)
        if path.startswith("/api/"):
            # XHR: a 302 to Microsoft would be swallowed by CORS and surface as a
            # mystery network error. 401 lets the client decide, same as hosted.
            #
            # The X-Auth-Login header is what makes the dev server work. On :5173 Vite
            # serves index.html for "/" itself, so the DOCUMENT request never reaches
            # FastAPI and the redirect above never fires — the SPA boots unauthenticated
            # and its first /api call is the only signal. api.ts redirects on a 401
            # carrying this header, and ONLY on that, so ordinary 401s elsewhere in the
            # app keep their existing meaning.
            return JSONResponse(
                {"detail": "not signed in; GET /.auth/login/aad"},
                status_code=401,
                headers={"X-Auth-Login": "/.auth/login/aad"},
            )
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        # To the LANDING page, not straight to Microsoft: an instant bounce left no
        # place for "sign out" to land (see signin_page) and no way to pick a different
        # account. One click further, and the click is the point.
        return RedirectResponse(f"/.auth/signin?next={urllib.parse.quote(target, safe='/?=&')}")
