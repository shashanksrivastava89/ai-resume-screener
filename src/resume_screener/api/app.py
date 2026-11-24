"""FastAPI application: the HTTP surface the React app consumes.

Route groups: meta (identity/jurisdictions/rbac), roles (+rubric+batch+screen),
review (+decisions), governance (signals/policies/audit), runs. Reads never touch
Azure; the two model-backed actions (rubric draft, screening) fail with a plain
message when Azure isn't reachable, exactly like the CLI doctor.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from resume_screener import dashboard_index, modes
from resume_screener.api import (decision_view, jobs, ratelimit, review, security_headers,
                                 service, verdict)
from resume_screener.dataroot import bootstrap
from resume_screener.fieldcrypto import DecisionKeyUnavailable
from resume_screener.models import Requisition, category_of

# P26: when uvicorn imports this module directly (the container CMD), resolve
# DATA_ROOT before any route touches a relative path. No-op locally.
bootstrap()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    # A previous process's in-flight jobs die with it; when job state lives in the DB
    # (jobs_backend=db) those rows would otherwise stay 'running' forever and block
    # new screens. Reap them once at startup (no-op for the in-memory backend).
    jobs.reap_orphans()
    # "Upload and screen" fires from HERE, in the API process, because its job store is
    # what the UI reads — a run spawned from the preparation worker could never be
    # tracked, paused or stopped. Started in lifespan (not at import) so the thread
    # belongs to the serving process, not to a module import in a test or the CLI.
    from resume_screener import batch_prep

    batch_prep.start_autoscreen_watchdog()
    yield


def _req_or_404(req_id: str) -> Requisition:
    req = service.find_requisition(req_id)
    if req is None:
        raise HTTPException(404, f"No role '{req_id}'.")
    return req


# The current request's effective capability set, stashed by middleware so the
# ONE per-req authorization point (_req_authorized) can enforce own-req scoping
# without threading caps through every route signature. None = no HTTP request
# in flight (CLI / tests calling helpers directly) => no extra scoping.
_REQUEST_CAPS: ContextVar[frozenset[str] | None] = ContextVar("request_caps", default=None)


def _request_caps() -> frozenset[str]:
    """The current request's effective capabilities, as the middleware resolved them.
    Empty outside an HTTP request — a caller with no viewer is granted nothing extra."""
    caps = _REQUEST_CAPS.get()
    return caps if caps is not None else frozenset()


def _own_req_denied(req: Requisition, user: str, caps: frozenset[str]) -> bool:
    """True when an own-reqs-scoped recruiter (recruiter WITHOUT reqs_all,
    2026-07-19) is looking at somebody else's requisition. Reqs they created, reqs
    where they're the assigned HM, and reqs the owner INVITED them onto (2026-09-10)
    stay visible; a legacy req with no created_by on record stays visible to every
    recruiter (honest fallback).

    ONE predicate decides this, and the dashboard's filter reads the same helper — a
    second copy of "is this mine" is how a person ends up able to open a requisition
    they cannot see, or see one they cannot open."""
    if "recruiter" not in caps or "reqs_all" in caps:
        return False
    return not _req_is_mine(req, user)


def _req_is_mine(req: Requisition, user: str) -> bool:
    """Is this requisition the person's own — created by them, assigned to them as
    hiring manager, invited onto by its owner, or old enough to have no owner?"""
    meta = service.req_meta(req)
    created = str(meta.get("created_by") or "").lower()
    if not created:
        return True                      # legacy req, no owner on record
    u = (user or "").lower()
    if created == u:
        return True
    hm = meta.get("hm") or {}
    if isinstance(hm, dict) and str(hm.get("email") or "").lower() == u:
        return True
    return service.is_collaborator(req, user)


def _sees_decisions(req: Requisition, user: str, caps) -> bool:
    """May this person see the requisition's hiring decisions — Move forward /
    Discuss / Not a fit, the note, the hiring manager's take (2026-10-21)? Its team
    only: an HR Head (`decisions_all`), or someone for whom the requisition is their
    own — its owner, its hiring manager, a collaborator. That second half IS
    _req_is_mine, the ownership rule, so the people who can work a requisition are
    the people who see its decisions. api/decision_view.py does the stripping."""
    from resume_screener import rbac

    return rbac.sees_all_decisions(caps or ()) or _req_is_mine(req, user)


def _team_or_403(req: Requisition, ident: tuple[str, str]) -> None:
    """Recording, reconfirming or sharing decisions is for the requisition's team —
    someone who cannot see its decisions must not make them blind (2026-10-21)."""
    if not _sees_decisions(req, ident[0], _request_caps()):
        raise HTTPException(403, decision_view.DECIDE_MESSAGE)


def _region_or_403(req: Requisition, user: str) -> None:
    """The region ACCESS lens (P38) for one requisition."""
    if not service.region_allows(req, user):
        raise HTTPException(
            403, "This requisition belongs to a region you don't have access to. "
                 "Ask IT to add the region to your account.")


def _req_authorized(req_id: str, ident: tuple[str, str]) -> Requisition:
    """Resolve a requisition (404) AND enforce the caller's region access (403),
    plus own-req scoping for recruiters without reqs_all (2026-07-19).

    Every per-req route flows through this (P38). Region is an ACCESS lens: when
    the region framework is unseeded (local dev) or the caller holds an all-regions
    grant, accessible_regions() is None = unrestricted, so this behaves exactly like
    a plain _req_or_404 — byte-identical to before enforcement existed."""
    req = _req_or_404(req_id)
    _region_or_403(req, ident[0])
    caps = _REQUEST_CAPS.get()
    if caps is not None and _own_req_denied(req, ident[0], caps):
        raise HTTPException(
            403, "This requisition was created by another recruiter — ask them "
                 "or the HR Head for access.")
    return req


# --- identity + RBAC enforcement (P27) ---------------------------------------------
#
# Every route below /api (except /api/health and /api/me) carries a capability
# dependency. Identity comes from the Easy-Auth principal header when hosted and
# DEV_USER/DEV_ROLE locally (rbac.get_identity); rbac.yaml maps role -> capabilities.

_NO_ACCESS = (
    "Your access isn't configured yet — ask IT to assign you one of the Resume "
    "Screener roles: HR Head, Legal, IT, TA Specialist, HR Specialist, Hiring "
    "Manager or Super Admin."
)


def identity(request: Request) -> tuple[str, str]:
    from resume_screener import rbac

    return rbac.get_identity(request.headers)


def require(capability: str, message: str | None = None):
    """Dependency factory: 403 unless the caller's (union) capability set holds
    `capability`. Resolves to (user, display_role) so handlers can consume the
    identity directly. Capabilities are the UNION across every role held (P36)."""

    def dependency(request: Request) -> tuple[str, str]:
        from resume_screener import rbac

        user, role, roles = rbac.get_identity_full(request.headers)
        caps = rbac.effective_capabilities(roles)
        if capability not in caps:
            # No capabilities at all = no/unknown role -> the friendly setup message.
            detail = message if (message and caps) else _NO_ACCESS
            raise HTTPException(403, detail)
        return user, role

    return dependency


def require_any(*capabilities: str, message: str | None = None):
    """Dependency factory: 403 unless the caller's union capability set holds AT
    LEAST ONE of `capabilities`. Used on routes shared by more than one audience —
    e.g. P41's HM-portal views (recruiter OR hm_take)."""

    def dependency(request: Request) -> tuple[str, str]:
        from resume_screener import rbac

        user, role, roles = rbac.get_identity_full(request.headers)
        caps = rbac.effective_capabilities(roles)
        if not any(c in caps for c in capabilities):
            detail = message if (message and caps) else _NO_ACCESS
            raise HTTPException(403, detail)
        return user, role

    return dependency


# Shared dependencies (rbac caps): recruiter pages = all four roles; view/edit
# rules and runs/audit per the Addendum 08 matrix.
def _recruiter(request: Request) -> tuple[str, str]:
    return require("recruiter")(request)


def _rules_view(request: Request) -> tuple[str, str]:
    return require("rules_view", "Viewing rules and policies is limited to the HR Head, "
                                 "Legal, IT and the Super Admin.")(request)


def _runs_audit(request: Request) -> tuple[str, str]:
    return require("runs_audit", "Runs & audit is limited to the HR Head and IT.")(request)


def _prefs_own(request: Request) -> tuple[str, str]:
    return require("prefs_own", "Managing screening preferences isn't part of your role.")(request)


def _prefs_approve(request: Request) -> tuple[str, str]:
    return require("prefs_approve", "Approving preferences is limited to the HR Head.")(request)


def _data_manage(request: Request) -> tuple[str, str]:
    return require("data_manage", "Data management is limited to the Super Admin.")(request)


# --- HM-portal access model (P41) ---------------------------------------------------
#
# The shared VIEW routes (roles list/detail, rubric GET, review list/detail, activity)
# are reachable by recruiters AND hiring managers. viewer_identity resolves the caller
# with their capability set so per-req scoping can tell an HM-only caller (hm_take
# without recruiter) apart: an HM sees ONLY the requisitions assigned to them.


def viewer_identity(request: Request) -> tuple[str, str, list[str]]:
    from resume_screener import rbac

    user, role, roles = rbac.get_identity_full(request.headers)
    caps = rbac.effective_capabilities(roles)
    # recruiter (full), hm_take (assigned-req scope) or reqs_read (read-only,
    # e.g. Legal from the audit trail) — all reach the VIEW routes; writes stay
    # recruiter/hm-gated on their own routes.
    if not ({"recruiter", "hm_take", "reqs_read"} & caps):
        detail = _NO_ACCESS if not caps else "You don't have access to requisitions."
        raise HTTPException(403, detail)
    return user, role, sorted(caps)


def _recruiter_or_read(request: Request) -> tuple[str, str]:
    """The READ side of recruiter-only routes — also open to a read-only
    requisition viewer (reqs_read: Legal, driven here by the audit trail), but
    NOT to hiring managers (these are recruiter-territory reads). Mutations keep
    the plain _recruiter guard, so a read-only viewer 403s on any write."""
    return require_any("recruiter", "reqs_read",
                       message="You don't have access to requisitions.")(request)


def _hm_only(caps: list[str]) -> bool:
    """A hiring manager with no recruiter capability — the scoped, suggest-only role."""
    return "hm_take" in caps and "recruiter" not in caps


def _deny_read_only(caps: list[str]) -> None:
    """A reqs_read-only viewer (Legal from the audit trail) READS every view
    route but must not WRITE through the ones that share viewer_identity
    (comments, reactions). 403 unless they hold recruiter or hm_take."""
    if not ({"recruiter", "hm_take"} & set(caps)):
        raise HTTPException(403, "Read-only access — you can view this requisition but not change it.")


def _req_view_authorized(req_id: str, user: str, caps: list[str]) -> Requisition:
    """404 + region 403 (via _req_authorized), plus HM scoping: an HM-only caller
    reaches ONLY requisitions whose sidecar hm.email is theirs."""
    req = _req_authorized(req_id, (user, ""))
    if _hm_only(caps):
        hm = service.req_meta(req).get("hm") or {}
        if not (isinstance(hm, dict) and str(hm.get("email", "")).lower() == user.lower()):
            raise HTTPException(403, "This requisition isn't assigned to you.")
    return req


def _hm_portal_or_403() -> None:
    """Defense in depth (P41): the whole HM-collaboration surface is dark unless the
    deployment enabled the hm_portal flag — on top of P37's capability suppression."""
    from resume_screener import feature_flags

    if not feature_flags.flag_enabled("hm_portal"):
        raise HTTPException(403, "The hiring-manager portal isn't enabled yet.")


def _assigned_hm(request: Request, req_id: str) -> tuple[str, str, Requisition]:
    """Dependency body for HM mutations: flag on, caller holds hm_take, and the
    caller is THIS requisition's assigned HM. Returns (user, role, req)."""
    _hm_portal_or_403()
    user, role = require("hm_take", "This action is for the assigned hiring manager.")(request)
    req = _req_or_404(req_id)
    _region_or_403(req, user)  # HM writes skipped the region lens (2026-10-16)
    hm = service.req_meta(req).get("hm") or {}
    if not (isinstance(hm, dict) and str(hm.get("email", "")).lower() == user.lower()):
        raise HTTPException(403, "This requisition isn't assigned to you.")
    return user, role, req


# The API's own description (/openapi.json) and its explorer (/docs, /redoc) are a
# map of every route — useful while building, and nothing a hosted deployment should
# hand out (InfoSec hardening, 2026-10-14). They answer only in APP_MODE=dev.
app = FastAPI(title="Resume Screener API", version="0.1.0", lifespan=_lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/openapi.json", include_in_schema=False)
def _openapi_dev_only() -> JSONResponse:
    if modes.resolve() != "dev":
        raise HTTPException(404, "Not found")
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def _api_docs_dev_only():  # noqa: ANN202
    from fastapi.openapi.docs import get_swagger_ui_html

    if modes.resolve() != "dev":
        raise HTTPException(404, "Not found")
    page = get_swagger_ui_html(openapi_url="/openapi.json", title="Resume Screener API")
    # The explorer loads its script from a CDN and starts it inline; it gets its own,
    # looser policy (dev only) instead of the app's.
    page.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
        "https://fastapi.tiangolo.com")
    return page

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

# Nothing this app sends was compressed (2026-09-10): the JS bundle is 469 kB, a
# review payload for 333 candidates is 391 kB and a 200-requisition dashboard is
# 212 kB — all of it JSON or text, all of it going to India over the public
# internet. The threshold keeps it off the small poll responses, where the CPU to
# compress would cost more than the bytes save on the one vCPU everything shares.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.exception_handler(DecisionKeyUnavailable)
async def _decision_key_unavailable(request: Request,
                                    exc: DecisionKeyUnavailable) -> JSONResponse:
    """A decision key that is configured but cannot be read refuses the WRITE rather
    than storing a verdict in plain text (fieldcrypto) — a 503 in plain words."""
    log.warning("Decision key unavailable on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={
        "detail": "Decisions can't be saved right now — the key that protects them "
                  "couldn't be read. Nothing was recorded; try again, or ask IT to "
                  "check the decision key."})


@app.middleware("http")
async def _stash_request_caps(request: Request, call_next):
    """Resolve the caller's effective capability set once per request and stash
    it (contextvar) for _req_authorized's own-req scoping — the route-level
    Depends() guards stay the primary gate; this only adds the reqs_all lens.

    Also the request's boundaries for two cross-cutting concerns:
      * the read memo (reqcache) — opened here, dropped on the way out, so every
        pure reader answers once per request and never outlives one;
      * timing — a Server-Timing header on every response (visible in the browser's
        Network panel) and a log line for anything over half a second. Until this
        existed the only latency signal was the platform's hourly average."""
    from resume_screener import rbac, reqcache

    started = time.perf_counter()
    memo = reqcache.begin()
    try:
        # Nothing below /api/ is capability-scoped: the SPA shell, its hashed assets
        # and the platform's health probe reach no authorised data, and the route
        # guards are the enforcement point regardless. Resolving an identity for them
        # was pure cost on the request path — and on 2026-09-07 it became an
        # expensive one, since the flag lookup stopped being a bare env read
        # (14% of all hosted traffic is the health probe alone).
        if _needs_capabilities(request.url.path):
            _user = ""
            try:
                _user, _role, roles = rbac.get_identity_full(request.headers)
                caps: frozenset[str] = frozenset(rbac.effective_capabilities(roles))
            except Exception:  # noqa: BLE001 — identity failures 403 at the route guards
                caps = frozenset()
            over = _general_budget_429(_user)
            if over is not None:
                security_headers.apply(over)
                return over
        else:
            caps = frozenset()
        token = _REQUEST_CAPS.set(caps)
        try:
            response = await call_next(request)
        finally:
            _REQUEST_CAPS.reset(token)
        response = await _maybe_304(request, response)
        security_headers.apply(response)  # after the 304, so a revalidation carries them
        elapsed_ms = (time.perf_counter() - started) * 1000
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        if elapsed_ms >= _SLOW_REQUEST_MS:
            log.warning("slow request: %s %s took %.0f ms", request.method, request.url.path, elapsed_ms)
        return response
    finally:
        reqcache.end(memo)


def _general_budget_429(user: str) -> JSONResponse | None:
    """One generous per-person budget on every /api/ request (2026-10-16, pen-test
    RS-09) on top of the per-route ones: a looping script or tab meets a 429 on
    whatever it calls. 600 a minute is far above a person (a page load is ~20
    requests, the live pollers peak near 150 a minute). The health probe and the
    SPA's assets never count."""
    if not ratelimit.enabled():
        return None
    wait = ratelimit.check("api", (user or "anonymous").strip().lower())
    if wait <= 0:
        return None
    return JSONResponse({"detail": ratelimit.MESSAGE}, status_code=429,
                        headers={"Retry-After": str(max(1, int(wait) + 1))})


# The health probe is served by the platform every few seconds and answers from
# nothing; /api/ is where every capability-scoped route lives.
_NO_CAPS_PATHS = frozenset({"/api/health"})


def _needs_capabilities(path: str) -> bool:
    return path.startswith("/api/") and path not in _NO_CAPS_PATHS


# The three GET paths a browser asks for over and over while a page sits open. Only
# these: reading a response body back means consuming its stream, which would break a
# résumé download or any other streamed reply, so the set is named rather than
# inferred (2026-09-10).
_POLL_PATHS = ("/api/notifications", "/api/screen/active")
_POLL_SUFFIX = "/comments"


def _is_poll(path: str) -> bool:
    return path in _POLL_PATHS or path.endswith(_POLL_SUFFIX)


async def _maybe_304(request: Request, response):
    """Answer a poll whose reply has not changed with 304 and no body.

    Honest about what this buys: the server has ALREADY done the work by the time
    this runs, and after the September/2026-09-10 passes that work is 3.8-4.8 ms and
    at most one query. What it saves is the body — and, more usefully, the client
    parsing and re-rendering a payload identical to the one it already holds.

    Scoped hard. It only touches GET 200s on the named poll paths, it never caches
    anything itself (the validator is a hash of the bytes just produced), and a
    response that is not a plain buffered body is passed through untouched."""
    if request.method != "GET" or response.status_code != 200:
        return response
    if not _is_poll(request.url.path):
        return response
    body_iter = getattr(response, "body_iterator", None)
    if body_iter is None:
        body = getattr(response, "body", None)
        if body is None:
            return response
    else:
        chunks = [chunk async for chunk in body_iter]
        body = b"".join(chunks)

    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = dict(response.headers)
    headers["ETag"] = etag
    # A poll's answer is per-caller (own-req scoping, the signed-in recipient), so it
    # must never be reused by a shared cache.
    headers["Cache-Control"] = "private, no-cache"
    if request.headers.get("if-none-match") == etag:
        headers.pop("content-length", None)
        headers.pop("content-encoding", None)
        return Response(status_code=304, headers=headers)
    headers["content-length"] = str(len(body))
    return Response(content=body, status_code=200, headers=headers,
                    media_type=response.media_type)


_SLOW_REQUEST_MS = 500

log = logging.getLogger("resume_screener.api")


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Any unhandled error becomes a parseable JSON 500 in plain recruiter
    language (the raw traceback goes to the server log only). HTTPExceptions
    never reach here — FastAPI's own handler serves those.

    Note: this runs inside Starlette's outermost ServerErrorMiddleware, so the
    response bypasses CORSMiddleware — fine in production (same-origin SPA);
    cross-origin Vite dev sees a generic fetch error instead."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail":
        "Something went wrong on our side. The details were logged — "
        "try again, and tell IT if it keeps happening."})

# Audit Explorer (P45): its own router, registered before the SPA catch-all. Its
# guards reuse this module's _runs_audit / _req_authorized lazily (no import cycle).
from resume_screener.api import access, explorer  # noqa: E402

app.include_router(explorer.router)
app.include_router(access.router)


# ============================ META ============================


@app.get("/api/health")
def health() -> dict:
    """Liveness probe for the container HEALTHCHECK / Container Apps — no auth,
    no file or DB reads.

    Also reports which build is answering. Everything else in this app is behind
    auth, including the static assets, so "did my image actually get deployed?"
    had no answer but the platform's own event log — which has been ambiguous in
    practice (a StoppingSite with no matching container-start line). APP_BUILD is
    baked in by the Dockerfile; empty locally."""
    return {"ok": True, "build": os.environ.get("APP_BUILD", "")}


@app.get("/api/me")
def get_me(request: Request) -> dict:
    from resume_screener import names, people, rbac
    from resume_screener.governance import last_review, review_pending

    user, role, roles = rbac.get_identity_full(request.headers)
    caps = sorted(rbac.effective_capabilities(roles))
    # Approval-queue badge (P39): the count of preferences awaiting this HR Head —
    # only computed for approvers so ordinary users never pay for it.
    pref_pending = 0
    if rbac.PREFS_APPROVE in caps:
        from resume_screener import preferences

        # what THIS approver can act on: their own preferences need someone else
        pref_pending = sum(1 for p in preferences.list_prefs()
                           if p["display_status"] in ("pending", "rereview")
                           and str(p.get("owner", "")).lower() != user.lower())
    # Change-proposal badge (P51): pending configuration changes awaiting a second
    # leader — only computed for approvers, mirroring pref_pending.
    changes_pending = 0
    if rbac.CHANGES_APPROVE in caps:
        from resume_screener import change_requests

        changes_pending = change_requests.pending_count()
    # The person's real name, for the identity block. Easy Auth carries a `name`
    # claim and rbac has always parsed it; nothing consumed it, so hosted users saw
    # a payroll UPN ("u115652@example.com"). The email-derived fallback is
    # used ONLY when the local-part is name-shaped (has separators, so it yields
    # "Arjun Test" — the dev/pilot case); a payroll id would humanize to "U115652",
    # which is worse than the address, so those keep the address.
    #
    # NOT the AD job title. `jobTitle` is a directory attribute, not a token claim:
    # surfacing it needs a claims-mapping policy or Graph User.Read.All + admin
    # consent (the ISM-274462 class of blocker). If that consent ever lands, this is
    # the one field to fill and `identityTitle` in the client picks it up.
    claim_name = rbac.principal_display_name(request.headers)
    # The people directory (2026-09-06): a real sign-in name is REMEMBERED, so every
    # later surface can call this person by name instead of their UPN — and if the
    # directory already holds a better record (Graph, when consented), that wins.
    # remember() writes only when something is new, so this is a dict lookup on
    # every request after the first.
    person = None
    if rbac.has_principal(request.headers):
        # The country too, when the app registration emits `ctry` (2026-09-12).
        # It opens their region picker somewhere sensible and decides nothing
        # else; absent is the ordinary case and changes nothing.
        person = people.remember(
            user, name=claim_name,
            country=rbac.principal_country(request.headers), source="sso")
    person = people.enrich(user) or person or people.lookup(user)
    if person and person.get("name"):
        claim_name = person["name"]
    if not claim_name and len(names.humanize_email(user).split()) > 1:
        claim_name = names.display_name(user)
    return {
        "user": user,
        "name": claim_name,
        # The directory job title (Graph when consented; "" until then — the client
        # falls back to the access scope) + the avatar the sidebar and every
        # <Person> render: a photo when the directory has one, else initials on a
        # stable per-person hue.
        "job_title": str((person or {}).get("job_title") or ""),
        "photo": (person or {}).get("photo"),
        "initials": people.initials(claim_name, user),
        "avatar_hue": people.avatar_hue(user),
        # Is anyone signed in at all? `known_role: false` covers TWO different
        # people — a real principal with no app role (needs an administrator) and
        # an expired/absent session (needs to sign in again). The client picks a
        # different screen for each. Always true in dev, where there is no sign-in.
        "authenticated": rbac.has_principal(request.headers) or not modes.auth_required(),
        "role": role,
        # Every known app role held; capabilities are their union (P36). The client
        # derives all UI (incl. the HM portal) from `capabilities`, not `role`.
        "roles": roles,
        # False = signed in (or dev) but no known app role assigned -> the client
        # shows the "access not configured" page instead of the app.
        "known_role": bool(caps),
        "capabilities": caps,
        "has_admin": rbac.has_admin_caps(caps),
        "can_edit_rules": rbac.RULES_EDIT in caps,
        "evaluation_mode": True,  # standing constraint until HR + Legal review (§0)
        "review_pending": review_pending(),
        "last_review": last_review(),
        "pref_pending": pref_pending,  # P39 approval-queue nav badge (0 for non-approvers)
        "changes_pending": changes_pending,  # P51 change-approval nav badge (0 for non-approvers)
        # Region base framework (Phase 7): the regions this user can access. P27's SSO
        # region-picker + hard enforcement consume this; today it's context only and
        # defaults to unrestricted when the framework isn't seeded.
        # What they can reach, what each region is CALLED, and where their lens
        # should open (2026-09-12) — `home`/`home_source` are a starting point,
        # never an access decision.
        "regions": service.me_regions(user),
        # True only when identity comes from the dev path (no SSO principal) and the
        # switch isn't disabled — the client then offers the user/role switcher so
        # demos can change users without an app restart. Gone once Easy Auth is on.
        "dev_switch": rbac.dev_switch_active(request.headers),
        # Which of the three entry paths this process is serving (APP_MODE): dev
        # (no sign-in) | uat (app-driven Entra) | prod (Easy Auth). The client BADGES
        # this rather than inferring the environment from the shape of the payload —
        # "am I looking at the real thing?" should never be a guess, for the reviewer
        # or for anyone reading a screenshot in a bug report.
        "mode": modes.describe(),
    }


@app.get("/api/notifications")
def get_notifications(request: Request, limit: int = 100) -> dict:
    """The signed-in user's notifications (2026-07-20): every `notify` event
    addressed to them, newest first, carrying the actor's real NAME (`from_name`)
    and the requisition TITLE (`req_title`) so the bell can group by req and show
    names instead of emails. Rows older than the admin-set expiry window are
    hidden here (the audit spine keeps them forever). Read state stays
    client-side (a last-seen timestamp)."""
    from datetime import datetime, timedelta, timezone

    from resume_screener import names, rbac
    from resume_screener.events import load_events

    user, _role, _roles = rbac.get_identity_full(request.headers)
    me = user.strip().lower()
    cfg = service.config()
    # Resolve only the requisitions this person's notifications actually name
    # (2026-09-10). Building the whole list cost a sidecar read per requisition in
    # the deployment, and the bell polls every 30 seconds from every open tab — 20%
    # of all hosted traffic — to render a handful of rows.
    meta_cache: dict[str, dict] = {}

    title_cache: dict[str, str | None] = {}

    def _req_title_of(req_id: str | None) -> str | None:
        if not req_id:
            return None
        if req_id not in title_cache:
            req = service.find_requisition(req_id, cfg)
            title_cache[req_id] = service.display_title(req) if req is not None else None
        return title_cache[req_id]

    def meta_of(req_id: str | None) -> dict | None:
        if not req_id:
            return None
        if req_id not in meta_cache:
            req = service.find_requisition(req_id, cfg)
            meta_cache[req_id] = service.req_meta(req) if req is not None else {}
        return meta_cache[req_id]

    expiry_days = service.notification_expiry_days()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=expiry_days)).isoformat(
        timespec="seconds")
    out = []
    for event in load_events(kinds=["notify"]):
        data = event.get("data") or {}
        if str(data.get("to", "")).strip().lower() != me:
            continue
        ts = event.get("ts", "")
        if ts and ts < cutoff:
            continue  # expired for the bell; still in the audit spine
        req_id = event.get("req_id")
        actor = event.get("actor", "")
        out.append({
            "ts": ts,
            "summary": event.get("summary", ""),
            "from": actor,
            "from_name": names.display_name(actor, req_meta=meta_of(req_id)),
            "req_id": req_id,
            "req_title": _req_title_of(req_id),
            "kind": str(data.get("kind", "")),
            # The deep-link payload (2026-07-19): ids only (candidate ids are
            # content hashes; comment/suggestion/change ids are opaque) — the
            # bell routes to the exact element that caused the notification.
            "data": {k: v for k, v in data.items() if k not in ("to", "kind")},
        })
    out.sort(key=lambda n: n["ts"], reverse=True)
    return {"notifications": out[: max(1, min(limit, 200))], "expiry_days": expiry_days}


@app.get("/api/settings/notifications")
def get_notification_settings(request: Request) -> dict:
    """The current notification-expiry window — any signed-in user may read it."""
    return {"expiry_days": service.notification_expiry_days()}


@app.put("/api/settings/notifications")
def set_notification_settings(payload: dict = Body(...),
                              ident: tuple[str, str] = Depends(require("flags_edit"))) -> dict:
    """Admin-configurable notification expiry (2026-07-20, SuperAdmin). Only the
    bell view is affected — the audit trail (events.jsonl) is never pruned."""
    try:
        days = int(payload.get("expiry_days"))
    except (TypeError, ValueError):
        raise HTTPException(400, "expiry_days must be a whole number of days.")
    if not 1 <= days <= 3650:
        raise HTTPException(400, "expiry_days must be between 1 and 3650.")
    service.set_app_setting("notification_expiry_days", days)
    return {"ok": True, "expiry_days": days}


@app.put("/api/me/home-region")
def put_home_region(request: Request, payload: dict = Body(...)) -> dict:
    """"Make this my default region" — where this person's lens opens.

    A DISPLAY preference, not access: the value must be a region they already
    reach, and choosing one can only ever narrow what they see. Clearing it
    (`null`) returns them to whatever the directory or their grant implies, and
    then to "All regions".

    Deliberately guarded by "is anyone signed in" rather than a capability: every
    role that can see a requisition list can have an opinion about which one it
    opens on."""
    from resume_screener import rbac, user_settings

    user, _role, roles = rbac.get_identity_full(request.headers)
    if not rbac.effective_capabilities(roles):
        raise HTTPException(403, _NO_ACCESS)

    raw = payload.get("region")
    region = str(raw).strip().upper() if raw else None
    ctx = service.region_context(user)
    if region:
        if not ctx.get("codes"):
            raise HTTPException(409, "Regions aren't set up on this deployment yet.")
        if region not in set(ctx["codes"]):
            raise HTTPException(400, "You don't have access to that region.")
    user_settings.set(user, "home_region", region)
    service.invalidate_region_context()
    return {"ok": True, "regions": service.me_regions(user)}


@app.get("/api/regions", dependencies=[Depends(require_any(
    "access_view", "flags_view", message="The region catalog is for administrators."))])
def get_regions() -> dict:
    """Seeded regions + their jurisdictions and enabled features (Phase 7, read-only)."""
    return {"regions": service.regions_catalog()}


# ---- people (2026-09-06): the directory behind every "who did this" ----------------


_people_read = require_any("recruiter", "hm_take", "reqs_read",
                           message="People lookup is limited to the recruiting team.")


@app.get("/api/people", dependencies=[Depends(_people_read)])
def people_search_route(q: str = "", limit: int = 8,
                        _rl: None = Depends(ratelimit.limited("people_search"))) -> dict:
    """People matching `q` for the hiring-manager picker and @-mentions: what the
    app already knows (everyone who has signed in, every typed HM), then Microsoft
    Graph when the deployment has enabled it and it answers. {people[], source,
    note} — `note` carries the plain reason when Graph was asked for but is not
    available, so the picker can say "type the address" instead of failing.

    At least two letters, at most ten answers, and a per-person budget
    (2026-10-16, pen-test RS-03): finding someone by name is the job; listing
    everyone a letter at a time is not."""
    from resume_screener import people

    if len(q.strip()) < people.SEARCH_MIN_CHARS:
        raise HTTPException(400, "Type at least two letters to search for someone.")
    return people.search(q, limit=max(1, min(int(limit), 10)))


@app.get("/api/bootstrap")
def get_bootstrap(request: Request) -> dict:
    """Everything the app shell needs, in ONE round trip.

    Opening any page fired seven requests in two dependent waves: identity first,
    then a burst of five, then one more that had waited for the burst. Each wave is a
    full round trip to India before anything renders, and the later ones queue behind
    the earlier ones on the single worker (2026-09-10).

    Every part is produced by the SAME handler that serves it on its own — this is a
    delivery envelope, never a second implementation — and the individual endpoints
    stay, because a client that has one of these already should not refetch the rest.

    A part the caller may not read is simply absent: the capability guards live on
    those handlers, so this asks each one and drops what it refuses. Nothing here
    grants access that /api/me would not."""
    from resume_screener import rbac

    out: dict = {"me": get_me(request)}
    caps = set(out["me"].get("capabilities") or [])

    def part(name: str, needs: set[str] | None, build):
        if needs is not None and not (caps & needs):
            return
        try:
            out[name] = build()
        except HTTPException:
            return          # not for this caller — the shell renders without it
        except Exception:   # noqa: BLE001 — one slow part must not cost the whole shell
            log.warning("bootstrap: %s unavailable", name, exc_info=True)

    user, role, _roles = rbac.get_identity_full(request.headers)
    part("features", set(_FEATURES_READERS), get_features)
    # The same capabilities as /api/people/directory's own guard: a sign-in with no
    # role used to receive the whole directory here, because calling the handler
    # directly skips its dependency (2026-10-16, pen-test RS-03).
    part("directory", {"recruiter", "hm_take", "reqs_read"},
         lambda: _directory_payload(user, sorted(caps)))
    part("notifications", None, lambda: get_notifications(request))
    part("active_screens", {"recruiter"}, lambda: screen_active_all((user, role)))
    return out


@app.get("/api/people/directory")
def people_directory_route(vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """The people this caller's pages name, for client-side name resolution: read
    once and cached by the client. Photos ride along only when the directory holds
    one."""
    return _directory_payload(vident[0], vident[2])


# Roles that see every requisition (or every audit row) see everyone those name.
_UNSCOPED_DIRECTORY = {"reqs_all", "reqs_read", "runs_audit", "access_view"}


def _directory_payload(user: str, caps: list[str]) -> dict:
    from resume_screener import people

    scope = _visible_people(user, caps)
    rows = sorted((people.public_row(r) for email, r in people.directory().items()
                   if scope is None or email.lower() in scope),
                  key=lambda r: (r["name"] or r["email"]).lower())
    return {"people": rows, **people.status()}


def _visible_people(user: str, caps: list[str]) -> set[str] | None:
    """Whose names this caller may resolve (2026-10-16, pen-test RS-03): the people
    on the requisitions they can see — owners, hiring managers, collaborators,
    whoever closed one, decided on one or did anything on one — and whoever signed
    off their own preferences. None = unrestricted. The directory used to go to
    every recruiter and hiring manager whole: everyone who had ever signed in, with
    job titles and photos. Built from what the caller can already see, so every name
    on their pages is in it; anyone else's falls back to the address."""
    from resume_screener import events, preferences

    if set(caps) & _UNSCOPED_DIRECTORY:
        return None
    cards = _visible_cards(service.config(), user, caps, fresh=False)
    ids = {c.get("req_id") for c in cards}
    out = {(user or "").lower()}

    def add(email) -> None:
        if email:
            out.add(str(email).strip().lower())

    for c in cards:
        add(c.get("created_by"))
        add((c.get("hm") or {}).get("email"))
        life = c.get("lifecycle") or {}
        add(life.get("closed_by"))
        for person in life.get("collaborators") or []:
            add(person.get("email"))
    _by_run, by_req = service._decision_index()
    for (rid, _cid), d in by_req.items():
        if rid in ids:
            add(d.reviewer)
    actors = events.actors_by_req()
    for rid in ids:
        out |= actors.get(rid, set())
    try:
        for rec in preferences.list_prefs(owner=user):
            add(rec.get("approved_by"))
            add(rec.get("actor"))
    except Exception:  # noqa: BLE001 — a store read never breaks name resolution
        pass
    return out


# Who reads the flag switches — the route's guard AND the bootstrap part (one set).
_FEATURES_READERS = ("recruiter", "hm_take", "reqs_read", "changes_approve")


@app.get("/api/features", dependencies=[Depends(require_any(*_FEATURES_READERS))])
def get_features() -> dict:
    """The merged feature-flag catalog (P37): file seeds every key, DB rows win per
    key they know. Readable by recruiters (History flag-gates Reopen/Clone), hiring
    managers (their portal derives from flags), and the read-only and approving
    roles, whose pages are flag-gated too.

    Without flags_view a caller gets each flag's switch and nothing else — key,
    state, regions, scope, control (2026-10-16, pen-test RS-08): the names,
    descriptions and sources are a roadmap of what is coming, for administrators."""
    from resume_screener import feature_flags

    flags = feature_flags.catalog()
    if "flags_view" not in (_request_caps() or ()):
        keep = ("key", "enabled", "default", "regions", "scope", "control")
        flags = [{k: f[k] for k in keep if k in f} for f in flags]
    return {"features": flags}


@app.put("/api/flags/{key}")
def put_flag(
    key: str,
    payload: dict = Body(...),
    ident: tuple[str, str] = Depends(
        require("flags_edit", "Editing feature flags is limited to a Super Admin.")),
) -> dict:
    """Publish a flag change (P37). {enabled: bool, region?: code}. Env-controlled
    flags are rejected (400) — their value comes only from the deployment."""
    from resume_screener import feature_flags

    if "enabled" not in payload:
        raise HTTPException(400, "Provide the new on/off value.")
    enabled = bool(payload.get("enabled"))
    region = payload.get("region")
    region = str(region).strip() if region else None
    try:
        entry = feature_flags.set_flag(key, region, enabled, ident[0])
    except feature_flags.FeatureError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "flag": entry}


@app.get("/api/jurisdictions", dependencies=[Depends(_rules_view)])
def get_jurisdictions() -> dict:
    from resume_screener.policy import PolicyError, _use_db, load_pack, resolve_policy

    cfg = service.config()
    default = cfg.policy.default_jurisdiction
    policies_dir = Path(cfg.policy.policies_dir)
    # Enumerate the packs from the DB when policy_backend=db, else the policies/ glob.
    if _use_db():
        from resume_screener.db.repositories import policy as policy_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            codes = policy_repo.list_pack_jurisdictions(session)
    else:
        codes = [p.stem for p in sorted(policies_dir.glob("*.yaml")) if p.stem != "base"]
    packs = []
    for code in codes:
        try:
            eff = resolve_policy(code, policies_dir)
            # the overlay's OWN fields (what the editor edits); stem = code, and the
            # DB loader keys on the stem, so this works under either backend.
            pack = load_pack(policies_dir / f"{code}.yaml")
        except PolicyError:
            continue
        packs.append({
            "code": code,
            "name": service.jurisdiction_display(code),
            "law": service.jurisdiction_law(code),
            "active": code == default,
            "badge": "Pilot default" if code == default else None,
            # effective = base ⊕ overlay (display); own_* = overlay only (editable)
            "obligations": eff.obligations,
            "keep_at_least_days": eff.keep_at_least_days,
            "purge_by_days": eff.purge_by_days,
            "monitoring_categories": eff.monitoring_categories,
            # Blocked patterns (2026-07-21): effective = universal floor ⊕ this
            # region's own; own_* = only this overlay's (what the editor edits).
            # Universal ones read floor:true/source:seed; regional ones are in own_*.
            "blocked_patterns": [p.model_dump() for p in eff.blocked_patterns],
            "own_blocked_patterns": [p.model_dump() for p in pack.blocked_patterns],
            "own_obligations": pack.obligations,
            "own_keep_at_least_days": pack.keep_at_least_days,
            "own_purge_by_days": pack.purge_by_days,
            "own_monitoring_categories": pack.monitoring_categories,
            # The regional DIAL (2026-09-12): which of the model's signals this
            # pack turns on or off, how far each may move a score, and its market
            # settings. Only this overlay's own, like every other own_* field —
            # the effective values (model ⊕ pack) come from
            # /api/scoring-model/effective, which derives them the way a run does.
            "own_signal_overrides": {
                sid: ov.model_dump(exclude_none=True)
                for sid, ov in (pack.signal_overrides or {}).items()
            },
            "version": pack.version,
        })
    return {"default": default, "jurisdictions": packs}


@app.post("/api/jurisdictions/{code}/derive")
def derive_jurisdiction_blocked_patterns(
    code: str, payload: dict = Body(default={}),
    ident: tuple[str, str] = Depends(require(
        "rules_edit", "Editing rules and policies is limited to the HR Head, Legal or IT.")),
) -> dict:
    """PROPOSE (no write) region-specific blocked patterns + a scoring-model
    conflict report for a jurisdiction, read from its rules text — the design's
    'AI-derive from rules'. The LLM only authors; the admin reviews/edits and
    submits the result as a normal policy_pack change proposal (a second leader
    approves it), and enforcement stays deterministic. Non-fatal: a model failure
    returns an empty proposal with a plain note."""
    from resume_screener import policy_derive
    from resume_screener.policy import PolicyError, load_pack
    from resume_screener.scoring_model import load_scoring_model

    if code == "base":
        raise HTTPException(400, "The base pack is the universal floor — derive region packs, "
                                 "not base.")
    policies_dir = Path(service.config().policy.policies_dir)
    try:
        pack = load_pack(policies_dir / f"{code}.yaml")
    except PolicyError as exc:
        raise HTTPException(404, str(exc))
    return policy_derive.derive_blocked_patterns(
        code, list(pack.obligations), pack.notes, load_scoring_model(),
        source_doc=(payload.get("source_doc") or None),
    )


def _pack_or_400(code: str | None, *, field: str = "job_location") -> str:
    """The job location a requisition will be screened under, or a plain refusal.

    There used to be a fallback here: a missing or unrecognised location became
    `config.policy.default_jurisdiction`, which is India. A Budapest requisition
    therefore got India's retention (1095/400 days instead of the EU's 365/180)
    and scored `language_evidence`, which the EU pack disables precisely because a
    language requirement can proxy nationality there — and nothing said a word.
    A guess about which law applies is not a convenience, so there is no guess.

    policy.py has held the same line for the engine since P4 ("other unknown
    locations are an error, never a silent default"); this moves it forward to
    where the recruiter can actually fix it."""
    from resume_screener import countries

    code = (code or "").strip()
    if not code:
        raise HTTPException(400, {
            "message": "Choose the job's country — it decides which hiring rules "
                       "apply and how long records are kept.",
            "field": field,
        })
    if code not in countries.pack_codes():
        raise HTTPException(400, {
            "message": f"'{code}' isn't a set of hiring rules this app knows. "
                       "Choose the job's country from the list.",
            "field": field,
        })
    return code


def _country_or_400(country: str | None, us_state: str | None, nyc: bool | None,
                    pack: str) -> tuple[str | None, str | None]:
    """Check the form's country against the pack it claims, and return both stored
    fields. A country we know but have no pack for is refused BY NAME — the
    recruiter learns "no rules for Canada yet", not "pick something else"."""
    from resume_screener import countries

    if not (country or "").strip():
        return None, (countries.state_code(us_state) if pack.startswith("US") else None)
    res = countries.resolve(country, us_state, nyc=nyc)
    if res.country is None:
        raise HTTPException(400, {
            "message": f"'{country}' isn't a country this app knows.",
            "field": "country",
        })
    if res.pack is None:
        raise HTTPException(400, {
            "message": f"There are no hiring rules for {res.country.name} yet — "
                       "ask HR and Legal to add them before screening a "
                       "requisition there.",
            "field": "country",
        })
    if res.pack != pack:
        raise HTTPException(400, {
            "message": "The country and the hiring rules don't match. Reload the "
                       "form and pick the country again.",
            "field": "country",
        })
    return res.country.alpha2, res.us_state


@app.get("/api/locations", dependencies=[Depends(require_any(
    "recruiter", "reqs_read", message="You don't have access to requisitions."))])
def get_locations() -> dict:
    """Everything the New-requisition form needs to ask "where is this job?".

    Three parts: the PACKS (a set of hiring rules, with its law and the two
    retention windows, so the form can state the consequences up front), the
    COUNTRIES that map to them, and the US STATES — because in the United States
    the law that applies is a state question and, for New York, a city one.

    The form asks for a country; the pack is a lookup, never something a recruiter
    has to know. Recruiter-guarded; the full pack payload stays on the
    rules_view-gated /api/jurisdictions."""
    from resume_screener.policy import PolicyError, _use_db, resolve_policy

    cfg = service.config()
    default = cfg.policy.default_jurisdiction
    policies_dir = Path(cfg.policy.policies_dir)
    if _use_db():
        from resume_screener.db.repositories import policy as policy_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            codes = policy_repo.list_pack_jurisdictions(session)
    else:
        codes = [p.stem for p in sorted(policies_dir.glob("*.yaml")) if p.stem != "base"]
    locations = []
    for code in codes:
        try:
            eff = resolve_policy(code, policies_dir)
        except PolicyError:
            continue
        locations.append({
            "code": code,
            "label": service.jurisdiction_display(code),
            "law": service.jurisdiction_law(code),
            "keep_at_least_days": eff.keep_at_least_days,
            "purge_by_days": eff.purge_by_days,
            # What the pack asks of the recruiter, in the pack's own plain words
            # (2026-09-06) — the "what are these rules?" popover reads these. The
            # obligations checklist is DISPLAYED, never enforced (§1).
            "obligations": list(getattr(eff, "obligations", []) or []),
            "notes": str(getattr(eff, "notes", "") or ""),
            "monitoring_categories": list(getattr(eff, "monitoring_categories", []) or []),
        })
    # Alphabetical. The pilot default used to sort first so a fresh picker landed
    # on it, which is how a recruiter anywhere in the world created an India
    # requisition by not touching the dropdown. Nothing is preselected now.
    locations.sort(key=lambda loc: loc["label"])
    from resume_screener import countries

    return {
        "packs": locations,
        "countries": countries.catalog(),
        "us_states": countries.state_catalog(),
        "nyc_pack": countries.NYC_PACK,
        "nyc_state": countries.NYC_STATE,
        # Where this deployment actually stores and processes the data, in plain
        # words, when the deployment says (PROCESSING_LOCATIONS). Empty = hidden;
        # nothing here is inferred, because guessing at data residency is worse
        # than saying nothing.
        "processing_locations": (os.environ.get("PROCESSING_LOCATIONS") or "").strip() or None,
    }


# --- TEMPORARY iCIMS bridge: careers-portal auto-fill (feature_flags.careers_autofill).
# Removable: delete this route, careers_lookup.py, the flag and the New-req onBlur
# handler to remove the feature (see careers_lookup.py header).
@app.get("/api/careers/lookup", dependencies=[Depends(_recruiter)])
def careers_lookup_route(req: str) -> dict:
    """Look a requisition up on the public careers site by iCIMS Req ID and return
    {status, title?, jd_text?, job_location?, source_url?, note?} to pre-fill the
    New-requisition form. Non-fatal by construction; off unless the deployment sets
    FEATURE_CAREERS_AUTOFILL."""
    from resume_screener import careers_lookup, feature_flags

    if not feature_flags.flag_enabled("careers_autofill"):
        return {"status": "disabled"}
    known = {loc["code"] for loc in get_locations()["packs"]}
    return careers_lookup.lookup(req, known_locations=known)


@app.post("/api/jd/parse")
def parse_jd_route(files: list[UploadFile],
                   ident: tuple[str, str] = Depends(_recruiter),
                   _rl: None = Depends(ratelimit.limited("jd_parse"))) -> dict:
    """Parse an uploaded JD file (PDF/DOCX) into {title, jd_text, job_location?} to
    pre-fill the New-requisition form. Permanent, always available (no flag).

    Sync def (runs in the threadpool): the extract/structure work is blocking, so
    keeping it off the event loop lets other requisitions stay responsive."""
    if not files:
        raise HTTPException(400, "Attach a JD file (PDF or DOCX).")
    upload = files[0]
    filename = upload.filename or "job-description"
    data = upload.file.read()
    try:
        text = service.extract_jd_text(filename, data)
    except service.JdParseError as exc:
        raise HTTPException(400, str(exc))
    known = {loc["code"] for loc in get_locations()["packs"]}
    return service.structure_jd(text, filename, known_locations=known)


@app.get("/api/rbac", dependencies=[Depends(
    require("access_view", "Access & Roles is limited to the Super Admin."))])
def get_rbac() -> dict:
    from resume_screener import rbac

    matrix = rbac.load_rbac()
    caps = [
        {"key": rbac.RECRUITER, "label": "Screen & review"},
        {"key": rbac.REQS_ALL, "label": "All requisitions"},
        {"key": rbac.RULES_VIEW, "label": "View rules"},
        {"key": rbac.RULES_EDIT, "label": "Propose rule changes"},
        {"key": rbac.SCORING_EDIT, "label": "Edit scoring model"},
        {"key": rbac.CHANGES_APPROVE, "label": "Approve changes"},
        {"key": rbac.RUNS_AUDIT, "label": "Runs & audit"},
        {"key": rbac.DATA_MANAGE, "label": "Manage data"},
        {"key": rbac.PREFS_OWN, "label": "Own preferences"},
        {"key": rbac.PREFS_APPROVE, "label": "Approve preferences"},
        {"key": rbac.ACCESS_VIEW, "label": "Access & Roles"},
        {"key": rbac.ACCESS_EDIT, "label": "Grant regions"},
        {"key": rbac.FLAGS_VIEW, "label": "View feature flags"},
        {"key": rbac.FLAGS_EDIT, "label": "Edit feature flags"},
        {"key": rbac.HM_TAKE, "label": "Hiring-manager take"},
    ]
    entra = {
        "HRHead": "Entra: ResumeScreener-HRHead",
        "Legal": "Entra: ResumeScreener-Legal",
        "IT": "Entra: ResumeScreener-IT",
        "TASpecialist": "Entra: ResumeScreener-TASpecialist",
        "HRSpecialist": "Entra: ResumeScreener-HRSpecialist",
        "HiringManager": "Entra: ResumeScreener-HiringManager",
        "SuperAdmin": "Entra: ResumeScreener-SuperAdmin",
    }
    rows = [{
        "name": role,
        "entra": entra.get(role, ""),
        "cells": {c["key"]: c["key"] in matrix.get(role, []) for c in caps},
    } for role in rbac.ROLES]
    return {"caps": caps, "rows": rows}


# ============================ ROLES ============================


def _card_is_mine(card: dict, user_lower: str) -> bool:
    """The dashboard's half of _req_is_mine, over an already-built card.

    Same rule, read from the card instead of the sidecar so the list costs no extra
    reads. `lifecycle.collaborators` is on the card for exactly this."""
    created = str(card.get("created_by") or "").lower()
    if not created:
        return True                      # legacy req, no owner on record
    if created == user_lower:
        return True
    if str((card.get("hm") or {}).get("email", "")).lower() == user_lower:
        return True
    people = (card.get("lifecycle") or {}).get("collaborators") or []
    return any(str(c.get("email") or "").lower() == user_lower for c in people)


def _visible_cards(cfg, user: str, caps: list[str], region: str | None = None, *,
                   fresh: bool = True) -> list[dict]:
    """The requisition cards this caller may see — THE rule, shared by the dashboard
    and the people directory's scope so the two can never disagree.

    fresh=False (the directory's scope, on every page load) takes the remembered
    cards without the index's validation pass: who owns a requisition, its hiring
    manager and its collaborators are written in this process, and those writes
    drop the row, so a stored card is current for everything the scope reads."""
    reqs = service.requisitions(cfg)
    if fresh:
        # Load in bulk what the loop below would otherwise ask for role by role
        # (2026-09-10) — the rubrics, the batch locks and the run index.
        service.prefetch_roles(reqs)
    # One broken role degrades to a "Needs attention" card — never the whole list.
    # Cards come from the remembered index where they are still valid; the builder is
    # passed in, so the index never becomes a second definition of a card.
    cards = dashboard_index.cards(cfg, reqs, service.role_summary_safe,
                                  **({} if fresh else {"budget": 0}))
    # Region is an ACCESS lens (P38): first hide anything outside the caller's
    # grant, then narrow to the picked lens. Unseeded framework => accessible None
    # => unrestricted (local dev unchanged). Metrics count the visible set.
    accessible = service.accessible_regions(user)
    if accessible is not None:
        # Own work is always visible, whatever the region lens says. Without this a
        # recruiter with no region grants saw an EMPTY dashboard — including the
        # requisition they had just created, which they then could not ask anyone
        # for access to, because they could not see it existed.
        u = (user or "").lower()

        def _mine(card: dict) -> bool:
            return _card_is_mine(card, u)

        cards = [c for c in cards if c["region"] in accessible or _mine(c)]
    if region:
        cards = [c for c in cards if c["region"] == region]
    # HM-only callers (P41): only the requisitions assigned to them.
    if _hm_only(caps):
        cards = [c for c in cards
                 if str((c.get("hm") or {}).get("email", "")).lower() == user.lower()]
    # Own-reqs-scoped recruiters (2026-07-19): TA/HR Specialists see the reqs
    # they created (or are the assigned HM on); legacy unowned reqs stay visible.
    elif "recruiter" in caps and "reqs_all" not in caps:
        u = user.lower()
        cards = [c for c in cards if _card_is_mine(c, u)]
    return cards


@app.get("/api/roles")
def list_roles(region: str | None = None,
               vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    user, _role, caps = vident
    cfg = service.config()
    cards = _visible_cards(cfg, user, caps, region)
    # dashboard aggregate metrics
    active = sum(1 for c in cards if c["stage"] != "complete")
    screened = sum(c["candidate_count"] for c in cards)
    # The person's OWN total (2026-09-06): résumés screened on requisitions they
    # created. The dashboard used to lead with the all-reqs total, which read as
    # "my work" to a recruiter and as nothing in particular to a lead.
    u_me = (user or "").lower()
    my_screened = sum(c["candidate_count"] for c in cards
                      if str(c.get("created_by") or "").lower() == u_me)
    awaiting = sum(1 for c in cards if c["stage"] in ("screened", "in_review"))
    open_cards = [c for c in cards if c["lifecycle"]["status"] == "active"]
    return {
        "metrics": {
            "active_roles": active, "candidates_screened": screened,
            "my_candidates_screened": my_screened,
            "awaiting_review": awaiting, "total": len(cards),
            # P29: the requisitions-list stat cards (P32 renders them)
            "need_action": sum(1 for c in open_cards
                               if c["stage"] in ("screened", "in_review")),
            # P41: open reqs with an INVITED hiring manager (awaiting their input).
            # A name-only HM on record (status null) isn't awaiting anything.
            "awaiting_hm": sum(1 for c in open_cards if (c.get("hm") or {}).get("status")),
            "suggested_close": sum(1 for c in open_cards if c["suggest_close"]["suggest"]),
            "history_count": len(cards) - len(open_cards),
        },
        # The batch-quality read is the AI's verdict on the pool — HR roles only.
        "roles": [verdict.role_for(c, caps, pool=not _is_hm_viewer(caps)) for c in cards],
    }


def _role_detail(cfg, req: Requisition) -> dict:
    # The place is auto-populated from the JD and not editable, so backfill it here for
    # requisitions created before intake captured it (local gazetteer sweep, persisted
    # once). Without this those reqs could never get a place at all — the UI no longer
    # offers a box to type one into.
    req = service.ensure_work_place(req)
    """The full role-detail payload — shared by GET /api/roles/{id} and the
    details PUT (which returns the updated role so the client needn't refetch)."""
    st = service.role_stage(cfg, req)
    return {
        "req_id": req.req_id,
        "title": service.display_title(req),
        "jd_ref": req.jd_ref,
        "job_location": req.job_location,
        "location_display": service.location_display(req),
        "country": req.country,
        "us_state": req.us_state,
        "law": service.jurisdiction_law(req.job_location),
        # The physical workplace + the recruiter's search radius (2026-08-20).
        # Deliberately separate from job_location, which is a JURISDICTION code that
        # resolves the policy pack. NON-SCORING: the client renders it as context and
        # offers it as a Review filter; nothing here reaches a score. `summary` ships
        # resolved so the "Within N km from …" wording lives in one place, and
        # `place_known` says whether distances can be worked out for it at all.
        "work_location": service.work_location_view(req),
        # Screening needs a JD (the checklist is drafted from it) AND an approved
        # checklist — the button stays disabled until both are present.
        "jd_present": service.jd_present(req),
        # Manual locks (2026-07-20): {checklist, candidates} each null or
        # {locked_by, locked_by_name, locked_at} — drive the lock badges + the
        # frozen-edit state on the Checklist/Candidates tabs.
        "locks": service.req_locks(req),
        **st,
        **service.req_v2_payload(cfg, req),
    }


@app.get("/api/roles/{req_id}")
def get_role(req_id: str,
             vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    cfg = service.config()
    req = _req_view_authorized(req_id, vident[0], vident[2])
    return verdict.role_for(_role_detail(cfg, req), vident[2],
                            pool=not _is_hm_viewer(vident[2]))


@app.post("/api/roles")
def create_role(payload: dict = Body(...), ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    from resume_screener.ats.local_folder import (
        _use_requisitions_db,
        req_meta_path,
        role_slug,
        save_req_meta,
    )

    cfg = service.config()
    title = str(payload.get("title", "")).strip()
    jd_text = str(payload.get("jd_text", "")).strip()
    icims_req_id = str(payload.get("icims_req_id", "") or "").strip() or None
    hm_name = str(payload.get("hm_name", "") or "").strip()
    hm_email = str(payload.get("hm_email", "") or "").strip()
    # The job's CITY for the non-scoring distance lens, normally pre-filled by
    # POST /api/jd/parse straight from the JD. Distinct from job_location, which is a
    # jurisdiction code choosing the policy pack. No radius yet: the recruiter sets that
    # on Overview once they see the shortlist, and until they do the lens just shows
    # each candidate's distance without filtering on it.
    work_place = str(payload.get("work_place", "") or "").strip()
    if not title:
        raise HTTPException(400, "A job title is required.")
    if not jd_text:
        raise HTTPException(400, "Paste the job description.")
    # Validated here, in the order the form asks: a missing location is the
    # recruiter's next field, not a reason to hide the two before it.
    location = _pack_or_400(str(payload.get("job_location", "")))
    country, us_state = _country_or_400(
        str(payload.get("country", "") or ""), str(payload.get("us_state", "") or ""),
        payload.get("nyc"), location)
    if hm_email and "@" not in hm_email:
        raise HTTPException(400, "That hiring-manager email doesn't look right — "
                                 "check it or leave it blank.")
    # The HM portal reaches a hiring manager THROUGH their email (no Graph/SSO
    # directory lookup yet), so a name without an email is unreachable.
    if hm_name and not hm_email:
        raise HTTPException(400, "Add the hiring manager's email — the HM portal "
                                 "reaches them through it. Or leave both blank.")
    # HM on record at creation (P-item 10): status null = named for reference,
    # NOT invited — "invited"/"review_requested" stay portal-only (assign_hm).
    # A Graph-API directory lookup resolves name→email here later.
    hm = {"email": hm_email, "name": hm_name, "status": None} if (hm_name or hm_email) else None
    # The iCIMS Req ID is the mapping anchor back to the real requisition —
    # required since P32, and one id maps to at most one requisition here.
    if not icims_req_id:
        raise HTTPException(400, "iCIMS Req ID is required — no mapping to the "
                                 "requisition is possible without it.")
    # Structured 409s: detail carries {message, field, req_id, title} so the form
    # can pin the error to the field AND deep-link to the existing requisition.
    clash = next((r for r in service.requisitions(cfg)
                  if service.icims_of(r) == icims_req_id), None)
    if clash:
        raise HTTPException(409, {
            "message": f"iCIMS Req ID '{icims_req_id}' is already used by "
                       f"'{service.display_title(clash)}'.",
            "field": "icims_req_id",
            "req_id": clash.req_id,
            "title": service.display_title(clash),
        })

    jd_dir = Path(cfg.ats.jd_dir)
    jd_dir.mkdir(parents=True, exist_ok=True)
    # Two requisitions may share a display TITLE (2026-07-20) — the iCIMS Req ID
    # is the unique key (checked above). The title-derived slug only SEEDS the
    # immutable req_id; a collision gets a numeric suffix ('accountant',
    # 'accountant-2', …) so the résumé batch and output tree never overlap, while
    # the JD heading still carries the real (possibly duplicate) title. A req_id
    # freed by deleting its role is reusable (delete removes every <stem>.* file).
    base = role_slug(title)
    stem = base
    suffix = 2
    while (jd_dir / f"{stem}.md").exists():
        stem = f"{base}-{suffix}"
        suffix += 1
    jd_path = jd_dir / f"{stem}.md"
    # first line becomes the role title (_jd_title reads the first markdown heading)
    jd_path.write_text(f"# {title}\n\n{jd_text}\n", encoding="utf-8")
    save_req_meta(
        jd_path,
        icims_req_id=icims_req_id,
        job_location=location,
        country=country, us_state=us_state,
        status="active", outcome=None, closed_at=None, closed_by=None,
        snooze_until=None,
        created_by=ident[0], created_at=_now(),
        jd_language=service.jd_language(jd_text),
        hm=hm,
        work_location=({"place": work_place, "radius_km": None, "max_km": 100}
                       if work_place else None),
    )
    if _use_requisitions_db():
        # The DB serves fetch_requisitions under this backend, so a role that is
        # only a FILE does not exist as far as the app is concerned: the list skips
        # it and every per-req route 404s — including the checklist draft this
        # wizard calls next, which then degrades to a blank checklist. Identity
        # rows are written here for the same reason the details edit syncs them
        # (lifecycle/hm/icims stay sidecar-only).
        from resume_screener.db.repositories import requisitions as req_repo
        from resume_screener.db.session import session_scope

        try:
            with session_scope() as session:
                req_repo.sync_new_role(
                    session, req_code=stem, role_title=title, job_location=location,
                    jd_path=jd_path, slug=base, created_by=ident[0],
                )
        except Exception:
            # Half-created is worse than not created: the JD file alone is an
            # invisible orphan (the symptom this write-through exists to prevent).
            # Undo the files and fail loudly rather than report a success the
            # recruiter cannot see.
            log.exception("create_role: requisition write-through failed for %s", stem)
            meta_path = req_meta_path(jd_path)
            for leftover in (meta_path.with_name(meta_path.name + ".lock"),
                             meta_path, jd_path):
                leftover.unlink(missing_ok=True)
            raise HTTPException(
                503, "The requisition couldn't be saved — the database didn't "
                     "respond. Nothing was created; try again in a moment."
            )
    if hm:
        # On record only — no invite, no notify (the portal's assign_hm does that).
        service.record_req_event(stem, ident[0], "hm_assigned",
                                 f"Hiring manager on record — {hm_name or hm_email}")
        if hm_name:
            from resume_screener import people

            people.remember(hm_email, name=hm_name, source="typed")
    return {"req_id": stem, "title": title, "job_location": location,
            "country": country, "us_state": us_state,
            "icims_req_id": icims_req_id, "hm": hm}


# --- requisition lifecycle (P29): quickview, close/reopen/snooze, events, trail -----


@app.get("/api/roles/{req_id}/quickview")
def quickview(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    """The Requisitions-list hover popover: a JD excerpt + the checklist and
    screening state at a glance."""
    req = _req_authorized(req_id, ident)
    rubric = service.rubric_for(req)
    try:
        jd_text = Path(req.jd_ref).read_text(encoding="utf-8")
    except OSError:
        jd_text = ""
    body = " ".join(line.strip() for line in jd_text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#"))
    body = re.sub(r"\s+", " ", body)
    return {
        "jd_excerpt": body[:300] + ("…" if len(body) > 300 else ""),
        "checklist_version": rubric.version if rubric else None,
        "requirement_count": len(rubric.requirements) if rubric else 0,
        "screened_count": len(service.read_candidate_records(req_id)),
        "added_by": service.req_meta(req).get("created_by"),
        "icims_req_id": req.icims_req_id,
    }


@app.get("/api/roles/{req_id}/jd")
def get_jd(req_id: str,
           vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """The FULL job description — the Overview panel and the checklist editor's
    reference pane. {title, jd_text} round-trips with the create/details forms.
    Reachable by recruiters and the assigned HM (it's the JD they wrote)."""
    req = _req_view_authorized(req_id, vident[0], vident[2])
    try:
        raw = Path(req.jd_ref).read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(404, "The job description file for this role is missing.")
    title, body = service.split_jd(raw)
    return {"title": title or service.display_title(req), "jd_text": body}


def _may_share_req(req: Requisition, user: str, caps: frozenset[str] | None) -> bool:
    """Who may change a requisition's collaborator list: its owner, or someone who
    can already see every requisition (HR Head, Super Admin).

    Deliberately NOT "anyone who can see it" — a collaborator passing the invitation
    on would turn a narrow, revocable grant into an unbounded one, and the owner
    would not know."""
    if caps and "reqs_all" in caps:
        return True
    created = str(service.req_meta(req).get("created_by") or "").strip().lower()
    return bool(created) and created == (user or "").strip().lower()


@app.post("/api/roles/{req_id}/collaborators")
def add_collaborator(req_id: str, payload: dict = Body(...),
                     ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Invite someone onto this requisition so they can see and work on it.

    Own-req scoping is the right default and the wrong absolute: cover, handovers and
    a second pair of eyes are ordinary recruiting, and the alternative was asking an
    HR Head to widen someone's whole role (2026-09-10). This grant is exactly as
    narrow as the need — one requisition, revocable, and on the record.

    It lifts own-req scoping ONLY. Capabilities still come from the person's role, so
    inviting someone who is not a recruiter grants them nothing; and the region lens
    is IT's, not the owner's, so it still applies."""
    user, _role = ident
    req = _req_authorized(req_id, ident)
    if not _may_share_req(req, user, _REQUEST_CAPS.get()):
        raise HTTPException(403, "Only the person who created this requisition "
                                 "(or an HR Head) can share it.")
    email = str(payload.get("email", "") or "").strip()
    name = str(payload.get("name", "") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(400, "A work email address is needed to share this requisition.")
    if email.lower() == str(service.req_meta(req).get("created_by") or "").lower():
        raise HTTPException(400, "That's the person who created this requisition — "
                                 "they already have access.")
    existing = service.collaborators(req)
    if any(c["email"].lower() == email.lower() for c in existing):
        return {"ok": True, "collaborators": existing}   # already shared; not an error

    from datetime import datetime, timezone

    from resume_screener import events, names, people
    from resume_screener.ats.local_folder import save_req_meta
    from resume_screener.notify import notify

    if name:
        people.remember(email, name=name, source="typed")
    entry = {"email": email, "name": name or people.display_name(email),
             "added_by": user,
             "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    updated = [*existing, entry]
    # save_req_meta drops this requisition's dashboard card as part of the write.
    save_req_meta(req.jd_ref, collaborators=updated)

    title = service.display_title(req)
    events.emit_event("req_shared", actor=user, req_id=req.req_id,
                      summary=f"{names.display_name(user)} shared {title} with "
                              f"{entry['name'] or email}.",
                      data={"email": email})
    notify(email, f"{names.display_name(user)} shared the requisition {title} with you.",
           actor=user, req_id=req.req_id, data={"kind": "req_shared"})
    return {"ok": True, "collaborators": updated}


@app.delete("/api/roles/{req_id}/collaborators/{email}")
def remove_collaborator(req_id: str, email: str,
                        ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Take someone off this requisition. The owner can remove anyone; a
    collaborator can remove THEMSELVES, which is how you decline or step away
    without having to ask."""
    user, _role = ident
    req = _req_authorized(req_id, ident)
    target = (email or "").strip().lower()
    if not (_may_share_req(req, user, _REQUEST_CAPS.get())
            or target == (user or "").strip().lower()):
        raise HTTPException(403, "Only the person who created this requisition "
                                 "(or an HR Head) can change who it's shared with.")
    from resume_screener import events, names
    from resume_screener.ats.local_folder import save_req_meta

    remaining = [c for c in service.collaborators(req) if c["email"].lower() != target]
    save_req_meta(req.jd_ref, collaborators=remaining)
    events.emit_event("req_unshared", actor=user, req_id=req.req_id,
                      summary=f"{names.display_name(user)} removed "
                              f"{names.display_name(email)} from "
                              f"{service.display_title(req)}.",
                      data={"email": target})
    return {"ok": True, "collaborators": remaining}


@app.put("/api/roles/{req_id}/details")
def update_role_details(req_id: str, payload: dict = Body(...),
                        ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Edit a role's details after creation (the wizard's Back path + future
    surfaces): title and/or JD text rewrite jds/<stem>.md ATOMICALLY under the
    file lock — the slug/req_id/filename NEVER change; location + iCIMS id merge
    into the sidecar. Returns the full updated role detail so the client needn't
    refetch."""
    from resume_screener.ats.local_folder import _use_requisitions_db, save_req_meta
    from resume_screener.locking import file_lock
    from resume_screener.policy import PolicyError, resolve_policy

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    recognized = ("title", "jd_text", "job_location", "icims_req_id",
                  "country", "us_state")
    provided = {k: str(payload.get(k, "")).strip() for k in recognized if k in payload}
    # work_location is a nested object, not a flat string, so it is validated on its own
    # rather than squeezed into `recognized`. Passing null clears it.
    wl_present = "work_location" in payload
    wl_new: dict | None = None
    if wl_present and payload.get("work_location") is not None:
        raw = payload["work_location"]
        if not isinstance(raw, dict):
            raise HTTPException(400, "work_location must be an object or null.")
        place = str(raw.get("place") or "").strip()
        if not place:
            raise HTTPException(400, "Add the job location — a distance needs somewhere "
                                     "to measure from. Or clear the location entirely.")
        try:
            max_km = max(1, int(raw.get("max_km") or 100))
            radius = raw.get("radius_km")
            radius_km = None if radius is None or radius == "" else int(radius)
        except (TypeError, ValueError):
            raise HTTPException(400, "The distance has to be a whole number of kilometres.")
        if radius_km is not None and not (0 <= radius_km <= max_km):
            raise HTTPException(400, f"Pick a distance between 0 and {max_km} km.")
        wl_new = {"place": place, "radius_km": radius_km, "max_km": max_km}
    # The hiring manager is plain requisition metadata — editable here regardless
    # of the HM-portal feature flag (the flag only gates COLLABORATION: invites,
    # takes, suggestions). Recording a name never invites or notifies.
    hm_present = "hm_name" in payload or "hm_email" in payload
    hm_name = str(payload.get("hm_name", "") or "").strip()
    hm_email = str(payload.get("hm_email", "") or "").strip()
    if not provided and not hm_present and not wl_present:
        raise HTTPException(400, "Nothing to update.")
    if hm_present and hm_email and "@" not in hm_email:
        raise HTTPException(400, "That hiring-manager email doesn't look right — "
                                 "check it or leave it blank.")
    if hm_present and hm_name and not hm_email:
        raise HTTPException(400, "Add the hiring manager's email — the HM portal "
                                 "reaches them through it. Or leave both blank.")
    if "title" in provided and not provided["title"]:
        raise HTTPException(400, "The job title can't be empty.")
    if "jd_text" in provided and not provided["jd_text"]:
        raise HTTPException(400, "The job description can't be empty.")
    if "job_location" in provided:
        # The same check creation runs: a pack this app actually has, never a
        # fallback. resolve_policy alone would accept any "US-XX" through its own
        # US-default fallback, which is right for the engine and too loose here.
        _pack_or_400(provided["job_location"])
        try:
            resolve_policy(provided["job_location"], cfg.policy.policies_dir)
        except PolicyError:
            raise HTTPException(400, "That job location isn't configured — pick one "
                                     "from the location list.")
        provided["country"], provided["us_state"] = _country_or_400(
            provided.get("country"), provided.get("us_state"), payload.get("nyc"),
            provided["job_location"])
    if "icims_req_id" in provided:
        if not provided["icims_req_id"]:
            raise HTTPException(400, "iCIMS Req ID is required — clearing it would "
                                     "break the mapping to the requisition.")
        clash = next((r for r in service.requisitions(cfg)
                      if r.req_id != req.req_id
                      and service.icims_of(r) == provided["icims_req_id"]), None)
        if clash:
            raise HTTPException(409, {
                "message": f"iCIMS Req ID '{provided['icims_req_id']}' is already "
                           f"used by '{service.display_title(clash)}'.",
                "field": "icims_req_id",
                "req_id": clash.req_id,
                "title": service.display_title(clash),
            })

    jd_path = Path(req.jd_ref)
    changed: list[str] = []
    new_title = new_body = None
    if "title" in provided or "jd_text" in provided:
        try:
            raw = jd_path.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(404, "The job description file for this role is missing.")
        old_title, old_body = service.split_jd(raw)
        new_title = provided.get("title") or old_title or service.display_title(req)
        new_body = provided["jd_text"] if "jd_text" in provided else old_body
        if "title" in provided and provided["title"] != (old_title or ""):
            changed.append("title")
        if "jd_text" in provided and provided["jd_text"] != old_body.strip():
            changed.append("job description")
        if changed:
            with file_lock(jd_path):
                tmp = jd_path.with_name(jd_path.name + ".tmp")
                tmp.write_text(f"# {new_title}\n\n{new_body}\n", encoding="utf-8")
                os.replace(tmp, jd_path)

    meta_updates: dict = {}
    if "job_location" in provided and provided["job_location"] != req.job_location:
        meta_updates["job_location"] = provided["job_location"]
        changed.append("job location")
    # The country rides with the pack: they are one answer to one question, and
    # storing a stale country beside a new pack would make the header say Hungary
    # over India's rules.
    if "job_location" in provided:
        for field in ("country", "us_state"):
            if provided.get(field) != getattr(req, field):
                meta_updates[field] = provided.get(field)
    if "icims_req_id" in provided and provided["icims_req_id"] != (service.icims_of(req) or ""):
        meta_updates["icims_req_id"] = provided["icims_req_id"]
        changed.append("iCIMS Req ID")
    if "jd_text" in provided and "job description" in changed:
        meta_updates["jd_language"] = service.jd_language(provided["jd_text"])
    if hm_present:
        existing_hm = service.req_meta(req).get("hm")
        # Preserve any portal status (invited/review_requested) when re-recording;
        # clearing both fields removes the HM from record.
        new_hm = ({"email": hm_email, "name": hm_name, "status": (existing_hm or {}).get("status")}
                  if (hm_name or hm_email) else None)
        if new_hm != existing_hm:
            meta_updates["hm"] = new_hm
            changed.append("hiring manager")
        if hm_name and hm_email:
            from resume_screener import people

            people.remember(hm_email, name=hm_name, source="typed")
    if wl_present:
        existing_wl = service.req_meta(req).get("work_location")
        if wl_new != existing_wl:
            meta_updates["work_location"] = wl_new
            changed.append("job location & distance" if wl_new else "location distance (cleared)")
    if meta_updates:
        save_req_meta(jd_path, **meta_updates)

    if changed and _use_requisitions_db():
        # Identity fields the DB backend serves must not drift — the ONE narrow
        # write-through (lifecycle/hm/icims stay sidecar-only, as close/reopen do).
        from resume_screener.db.repositories import requisitions as req_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            req_repo.sync_role_details(
                session, req.req_id,
                role_title=new_title if "title" in changed else None,
                job_location=meta_updates.get("job_location"),
                jd_title=new_title if new_title is not None else None,
                jd_text=new_body if "job description" in changed else None,
            )

    if changed:
        service.record_req_event(req_id, ident[0], "req_details_updated",
                                 "Details updated — " + ", ".join(changed),
                                 data={"changed": changed})

    req = _req_or_404(req_id)  # re-resolve so the new title/location flow through
    caps = _request_caps()
    return verdict.role_for(_role_detail(cfg, req), caps, pool=not _is_hm_viewer(caps))


@app.post("/api/roles/{req_id}/close")
def close_role(req_id: str, payload: dict = Body(...),
               ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Close the requisition IN THIS WORKSPACE ONLY (the iCIMS req is unchanged) —
    it moves to History, stays fully readable, and starts the retention clock.

    Closing also ENDS the hiring work (2026-10-16, pen-test RS-06): the status is
    written first, so the auto-screen watchdog can't start a run in the gap, then a
    running or paused screen is cancelled — whoever started it, because closing is
    the larger, audited action — and preparation stops, along with any run an
    upload asked for. From then on every hiring-work route answers 409
    (_open_req_or_409)."""
    from resume_screener.ats.local_folder import save_req_meta

    req = _req_authorized(req_id, ident)
    if service.is_closed(req):
        raise HTTPException(400, "This requisition is already closed.")
    kind = str(payload.get("outcome", "")).strip()
    if kind not in ("hired", "cancelled"):
        raise HTTPException(400, "Outcome must be 'hired' or 'cancelled'.")
    outcome: dict = {"kind": kind}
    if kind == "hired":
        try:
            outcome["hired_count"] = max(1, int(payload.get("hired_count") or 1))
        except (TypeError, ValueError):
            raise HTTPException(400, "Hired count must be a number.")
    user = ident[0]
    save_req_meta(req.jd_ref, status="closed", outcome=outcome,
                  closed_at=_now(), closed_by=user)
    stopped = _stop_work_on_close(service.config(), req, user)
    summary = (f"Closed — hired {outcome['hired_count']}" if kind == "hired"
               else "Closed — no hire (cancelled)")
    if stopped["screening"]:
        summary += " · stopped the running screen"
    if stopped["preparing"]:
        summary += " · stopped preparing résumés"
    service.record_req_event(req_id, user, "req_close", summary)
    body = {"ok": True, "status": "closed", "outcome": outcome,
            "stopped": {"screening": stopped["screening"], "preparing": stopped["preparing"]}}
    if stopped["note"]:
        body["note"] = stopped["note"]
    return body


def _stop_work_on_close(cfg, req, user: str) -> dict:
    """Cancel the screen and stop preparation for a requisition being closed."""
    from resume_screener import batch_prep

    out = {"screening": False, "preparing": False, "note": None}
    lock = jobs.screen_lock(req.req_id)
    if lock is not None:
        outcome, _job = jobs.cancel_job(lock["job_id"], user, intent="cancel")
        if outcome in ("stopping", "cancelled"):
            out["screening"] = True
        elif outcome == "not_here":
            out["note"] = ("A screening run started elsewhere is still going — cancel it "
                           "from the Candidates tab.")
    folder = service.batch_folder(cfg, req)
    prep = batch_prep.session_payload(folder)
    if prep.get("active"):
        batch_prep.request_stop(folder, req.req_id)
        out["preparing"] = True
    elif prep.get("auto_screen"):
        batch_prep.clear_autoscreen(folder, "The requisition was closed — no screening started.")
    return out


@app.post("/api/roles/{req_id}/reopen")
def reopen_role(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Back to active. Gated on the `icims_reopen` feature flag for the req's region
    (P37) — off by default, so the History button stays disabled until it's on."""
    from resume_screener import feature_flags
    from resume_screener.ats.local_folder import save_req_meta

    req = _req_authorized(req_id, ident)
    if not feature_flags.flag_enabled("icims_reopen", service.region_of_req(req)):
        raise HTTPException(403, "Reopening is turned off — ask IT to enable it (feature flag).")
    if not service.is_closed(req):
        raise HTTPException(400, "This requisition is already open.")
    user = ident[0]
    save_req_meta(req.jd_ref, status="active", outcome=None,
                  closed_at=None, closed_by=None)
    service.record_req_event(req_id, user, "req_reopen", "Reopened")
    return {"ok": True, "status": "active"}


@app.delete("/api/roles/{req_id}")
def delete_role(req_id: str, payload: dict = Body(default={}),
                ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Permanently delete a requisition and everything scoped to it (JD +
    sidecars, run output, candidate batch). Irreversible — the client confirms
    with the loss counts first; `confirm` must be true so a stray call can't wipe
    a req. The iCIMS requisition is unchanged. Records a governance-visible event
    BEFORE removing the files (so the trail survives the delete)."""
    from resume_screener.ats.local_folder import _use_requisitions_db

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    if not payload.get("confirm"):
        raise HTTPException(400, "Deletion must be confirmed.")
    title = service.display_title(req)
    code = service.icims_of(req) or "—"
    # Emit first — events.jsonl is separate from the deleted files, so the record
    # of who deleted what persists in the Audit Explorer.
    service.record_req_event(
        req_id, ident[0], "req_deleted",
        f"Deleted requisition '{title}' (iCIMS {code}) and all its screening data")
    counts = service.delete_requisition(cfg, req)
    if _use_requisitions_db():
        # The files are gone; leaving the row would keep the requisition listed
        # with nothing behind it. Runs/jobs keep their (SET NULL) history.
        from resume_screener.db.repositories import requisitions as req_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            req_repo.delete_requisition(session, req.req_id)
    return {"ok": True, "deleted": req_id, **counts}


@app.post("/api/roles/{req_id}/clone")
def clone_role(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Prefill for a NEW requisition seeded from this one (P37). Gated on the
    `icims_clone` flag for the req's region. Returns {title, jd_text, job_location,
    checklist}; the client lands on /reqs/new with these filled and the iCIMS Req ID
    deliberately BLANK (always entered fresh). No requisition is created here."""
    from resume_screener import feature_flags

    req = _req_authorized(req_id, ident)
    if not feature_flags.flag_enabled("icims_clone", service.region_of_req(req)):
        raise HTTPException(403, "Cloning is turned off — ask IT to enable it (feature flag).")
    jd_text = ""
    try:
        # Body only — the create route re-composes '# {title}\n\n{body}', so
        # prefilling the raw file would nest a second heading.
        _title, jd_text = service.split_jd(Path(req.jd_ref).read_text(encoding="utf-8"))
    except OSError:
        pass
    rubric = service.rubric_for(req)
    checklist = _rubric_to_ui(rubric)["requirements"] if rubric else []
    # Fresh rows — strip the source ids so the new checklist re-numbers on save.
    checklist = [{"text": r["text"], "type": r["type"], "kind": r["kind"],
                  "importance": r["importance"]} for r in checklist]
    return {
        "title": service.display_title(req),
        "jd_text": jd_text,
        "job_location": req.job_location,
        "country": req.country,
        "us_state": req.us_state,
        "checklist": checklist,
    }


@app.post("/api/roles/{req_id}/snooze")
def snooze_role(req_id: str, payload: dict = Body(default={}),
                ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """"Keep open" on the close suggestion — quiet it for N days (default 30)."""
    from resume_screener.ats.local_folder import save_req_meta

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    raw_days = (payload or {}).get("days")
    try:
        days = 30 if raw_days is None else int(raw_days)
    except (TypeError, ValueError):
        raise HTTPException(400, "Days must be a number.")
    if days < 1:
        raise HTTPException(400, "Days must be at least 1.")
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    user = ident[0]
    save_req_meta(req.jd_ref, snooze_until=until)
    service.record_req_event(req_id, user, "req_snooze",
                             f"Close suggestion snoozed for {days} days")
    return {"ok": True, "snooze_until": until}


# The only frontend-fired events this route will record — anything else is a 400.
_FE_EVENT_ALLOWLIST = {"req_history_view": "Viewed the closed requisition's history"}


@app.post("/api/roles/{req_id}/events")
def record_fe_event(req_id: str, payload: dict = Body(...),
                    ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    _req_authorized(req_id, ident)
    action = str(payload.get("action", "")).strip()
    if action not in _FE_EVENT_ALLOWLIST:
        raise HTTPException(400, "That event isn't recognised.")
    service.record_req_event(req_id, ident[0], action, _FE_EVENT_ALLOWLIST[action])
    return {"ok": True}


@app.get("/api/roles/{req_id}/trail")
def req_trail(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    """The workspace Audit tab: created / checklist versions / runs / lifecycle
    events plus the decision summary with verbatim reasons."""
    req = _req_authorized(req_id, ident)
    return service.req_trail(req)


# --- rubric (screening checklist) --------------------------------------------------


def _pack_exclusions() -> list[str]:
    """The union of every shipped pack's additional_exclusions (P43) — the
    checklist critic escalates these to WARN, same as HR preferences (P39).
    Empty today; a forward hook."""
    from resume_screener import preferences

    try:
        return preferences.pack_exclusion_keywords(service.config().policy.policies_dir)
    except Exception:  # noqa: BLE001 — a policy read must never break the editor
        return []


def _blocked_patterns_for(req) -> list:
    """The checklist critic's jurisdiction floor for THIS req (2026-07-21).

    A checklist row is a job-level rule under the req's job location, so it is
    classified against that jurisdiction's resolved blocked patterns — a factor
    blocked only in California is blocked for California reqs, warn elsewhere.
    Any policy read failure degrades to the universal base floor, never crashes
    the editor."""
    from resume_screener import policy

    policies_dir = service.config().policy.policies_dir
    try:
        return policy.resolve_policy(req.job_location, policies_dir).blocked_patterns
    except Exception:  # noqa: BLE001 — legacy/empty location, unknown pack, etc.
        try:
            return policy.base_blocked_patterns(policies_dir)
        except Exception:  # noqa: BLE001
            return []


def _as_requirement_reason(reason: str) -> str:
    """The policy pack writes its blocked-topic sentences for PREFERENCES ("…
    Preferences can never be based on it."), because that is where the classifier
    started. A checklist row is not a preference, and reading someone else's noun
    back at you is exactly the kind of thing the pilot flagged as hard to follow —
    so the requirement surfaces say it in their own words. The pack stays the source
    of truth; only the noun changes (2026-09-07)."""
    return (reason or "").replace("Preferences", "Requirements").replace("preferences", "requirements")


def _rubric_to_ui(rubric, blocked_patterns: list | None = None) -> dict:
    """One ordered list (P33). Importance 1-5 maps from the normalized non-gate
    weights; gates carry no weight, so they ship a neutral 3 — the editor keeps
    whatever the recruiter typed within the session. Each row carries the P43
    critic verdict: critic_level ("warn"|null, persisted) + critic_reason — the
    sentence stored with the flag when there is one (a flag the intent screen
    raised cannot be recomputed from the phrase lists), else recomputed against
    the req's jurisdiction floor."""
    from resume_screener.prefcheck import classify_text

    reqs = rubric.normalized().requirements
    max_w = max((r.weight for r in reqs), default=1.0) or 1.0
    extra = _pack_exclusions()
    rows = []
    for r in reqs:
        reason = ""
        if r.critic_level == "warn":
            reason = r.critic_reason or classify_text(
                r.text, blocked_patterns=blocked_patterns, extra_exclusions=extra,
                stage="checklist",
            ).reason
        rows.append({
            "id": r.id, "text": r.text, "type": r.type, "kind": r.kind,
            # Which of the three checklist groups this row is filed under
            # (2026-08-20). Derived from `type` HERE rather than in the client so the
            # mapping has one home (models.CATEGORY_OF_TYPE) and an old rubric on disk
            # groups correctly without migration.
            "category": category_of(r.type),
            "importance": (3 if r.kind == "hard_gate"
                           else max(1, min(5, round(r.weight / max_w * 5)))),
            "critic_level": r.critic_level,
            "critic_reason": _as_requirement_reason(reason),
        })
    return {"version": rubric.version, "role_title": rubric.role_title, "requirements": rows}


def _rubric_ui(req: Requisition) -> dict:
    """The checklist UI payload for a requisition (shared by the GET route and the
    recruiter save route, so they can't drift). When the latest version has WARN
    rows and hasn't been signed off, it carries an `approval` block so the editor
    routes publish through HR-Head approval (P43)."""
    rubric = service.rubric_for(req)
    if rubric is None:
        return {"version": None, "requirements": []}
    ui = _rubric_to_ui(rubric, _blocked_patterns_for(req))
    # service.checklist_gate is the ONE P43 source — the same record that blocks
    # /screen is what puts the approval banner on the editor.
    gate = service.checklist_gate(req, rubric)
    if gate["approval"] is not None:
        ui["approval"] = gate["approval"]
    return ui


@app.get("/api/roles/{req_id}/rubric")
def get_rubric(req_id: str,
               vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    return _rubric_ui(_req_view_authorized(req_id, vident[0], vident[2]))


@app.post("/api/roles/{req_id}/checklist/check")
def check_requirement_route(req_id: str, payload: dict = Body(...),
                            ident: tuple[str, str] = Depends(_recruiter),
                            _rl: None = Depends(ratelimit.limited("checklist_check"))) -> dict:
    """Dry-run the checklist critic on one row (the design's "Check & add") —
    the SAME fairness screen (phrase lists + the intent screen) and
    jurisdiction-pack exclusions the publish route enforces, with no write. The
    client shows blocked/warn before committing; publish re-checks server-side
    either way."""
    from resume_screener.prefcheck_semantic import screen_texts

    req = _req_authorized(req_id, ident)
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Type a requirement first.")
    verdict = screen_texts([text], stage="checklist", blocked_patterns=_blocked_patterns_for(req),
                           extra_exclusions=_pack_exclusions())[0]
    return {"level": verdict.level, "reason": _as_requirement_reason(verdict.reason),
            "semantic": verdict.semantic}


@app.post("/api/roles/{req_id}/rubric/draft")
def draft_rubric_route(req_id: str, ident: tuple[str, str] = Depends(_recruiter),
                       _rl: None = Depends(ratelimit.limited("rubric_draft"))) -> dict:
    from resume_screener.azure_clients import get_openai_client
    from resume_screener.config import ConfigError, api_keys_for, load_settings
    from resume_screener.rubric import RubricError, draft_rubric

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    try:
        settings = load_settings()
        keys = api_keys_for(settings)
        info = get_openai_client(settings, keys)
        jd_text = Path(req.jd_ref).read_text(encoding="utf-8")
        rubric = draft_rubric(jd_text, req.jd_ref, info.client, settings.azure_openai_chat_deployment)
    except (ConfigError, RubricError) as exc:
        raise HTTPException(503, str(exc))
    return _rubric_to_ui(rubric, _blocked_patterns_for(req))


_REQUIREMENT_KINDS = {"nice_to_have", "must_have", "hard_gate"}


@app.put("/api/roles/{req_id}/rubric")
def save_rubric_route(req_id: str, payload: dict = Body(...),
                      ident: tuple[str, str] = Depends(_recruiter),
                      _rl: None = Depends(ratelimit.limited("checklist_check"))) -> dict:
    from resume_screener.models import Requirement, Rubric
    from resume_screener.prefcheck import classify_text
    from resume_screener.prefcheck_semantic import screen_texts
    from resume_screener.rubric import RubricError, next_version, save_rubric

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _manual_lock_409(req, "checklist")  # a manually-locked checklist is frozen
    rows = payload.get("requirements") or []
    if not rows:
        raise HTTPException(400, "A checklist needs at least one requirement.")
    extra = _pack_exclusions()
    blocked = _blocked_patterns_for(req)  # the req's jurisdiction floor (per-req)
    requirements = []
    for row in rows:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        kind = str(row.get("kind", "nice_to_have"))
        if kind not in _REQUIREMENT_KINDS:
            raise HTTPException(400, f"Unknown requirement kind '{kind}'.")
        # The SAME critic HR preferences run (P39/P43). Blocked text can never be
        # saved — 400 before any version bump, file write or model call; warned
        # text is saved but flagged and routed through HR-Head approval.
        verdict = classify_text(text, blocked_patterns=blocked, extra_exclusions=extra,
                                stage="checklist")
        if verdict.level == "blocked":
            raise HTTPException(
                400,
                f"This requirement can't be used. {_as_requirement_reason(verdict.reason)}",
            )
        requirements.append(Requirement(
            # ids re-assigned in submitted order — the single list IS the order
            id=f"r{len(requirements) + 1:02d}", text=text, type=row.get("type", "other"),
            kind=kind,
            weight=0.0 if kind == "hard_gate" else float(row.get("importance", 3)),
        ))
    if not requirements:
        raise HTTPException(400, "Every requirement was blank.")
    # Then the intent screen, ONE call for the whole checklist (2026-10-16): it can
    # only escalate, and when it can't run every row waits for HR-Head approval.
    verdicts = screen_texts([r.text for r in requirements], stage="checklist",
                            blocked_patterns=blocked, extra_exclusions=extra)
    for r, verdict in zip(requirements, verdicts):
        if verdict.level == "blocked":
            raise HTTPException(
                400,
                f"This requirement can't be used. {_as_requirement_reason(verdict.reason)}",
            )
        if verdict.level == "warn":
            r.critic_level = "warn"
            r.critic_reason = verdict.reason
    rubric = Rubric(
        # Stamp the CURRENT JD heading — pinning the first version's title
        # forever would hide later title edits (PUT /details) from every card.
        role_title=req.role_title,
        jd_ref=req.jd_ref, requirements=requirements,
    )
    saved_version = next_version(req.jd_ref)  # the version this save will assign
    try:
        save_rubric(rubric, req.jd_ref)
    except RubricError as exc:
        raise HTTPException(400, str(exc))

    # Every successful save is a publish on the app spine (feeds the P41 activity
    # rail's checklist history and the P45 audit explorer's rules scope).
    service.record_req_event(req_id, ident[0], "checklist_publish",
                             f"Checklist v{saved_version} published")
    # Reconcile the approval record to the just-saved version.
    from resume_screener import checklist_approval

    flagged = [r.id for r in requirements if r.critic_level == "warn"]
    checklist_approval.record_publish(req.jd_ref, version=saved_version, flagged_ids=flagged)

    ui = _rubric_ui(_req_authorized(req_id, ident))
    ui["needs_approval"] = bool(flagged)
    return ui


@app.get("/api/roles/{req_id}/rubric/versions")
def rubric_versions(req_id: str,
                    vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """This checklist's published versions, newest first — when, by whom, how big,
    and whether anyone was ever screened against each one (2026-09-07). The
    Checklist tab's version history reads it; "restore" is a read here plus the
    ordinary publish, so history is never rewritten."""
    req = _req_view_authorized(req_id, vident[0], vident[2])
    return {"versions": service.rubric_versions(req)}


@app.get("/api/roles/{req_id}/rubric/versions/{version}")
def rubric_version_detail(req_id: str, version: int,
                          vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """One published version's requirements, frozen as it was published — through
    the editor's own row converter, so an old version reads back exactly the way
    the current one does."""
    req = _req_view_authorized(req_id, vident[0], vident[2])
    rubric = service.rubric_version(req, version)
    if rubric is None:
        raise HTTPException(404, f"This checklist has no version {version}.")
    ui = _rubric_to_ui(rubric, _blocked_patterns_for(req))
    return {"version": version, "requirements": ui["requirements"]}


# --- checklist approval (P43) ------------------------------------------------------
#
# A checklist whose text the critic flags WARN can't be *used* until the HR Head
# signs off. The recruiter requests; only rules_edit (the HR Head) approves.


def _current_checklist_version(req: Requisition) -> int:
    rubric = service.rubric_for(req)
    if rubric is None or rubric.version is None:
        raise HTTPException(400, "This role has no checklist yet.")
    return rubric.version


@app.post("/api/roles/{req_id}/checklist-approval/request")
def request_checklist_approval(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    from resume_screener import checklist_approval

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    version = _current_checklist_version(req)
    record = checklist_approval.request_approval(req.jd_ref, version=version, requested_by=ident[0])
    if record is None:
        raise HTTPException(400, "This checklist version isn't awaiting a request.")
    service.record_req_event(req_id, ident[0], "checklist_approval_requested",
                             f"Requested HR-Head approval for checklist v{version}")
    return {"ok": True, "approval": record}


@app.post("/api/roles/{req_id}/checklist-approval/approve")
def approve_checklist(
    req_id: str,
    ident: tuple[str, str] = Depends(
        require("rules_edit", "Approving flagged checklists is limited to the HR Head.")
    ),
) -> dict:
    from resume_screener import checklist_approval

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    version = _current_checklist_version(req)
    record = checklist_approval.approve(req.jd_ref, version=version, approved_by=ident[0])
    if record is None:
        raise HTTPException(400, "This checklist version isn't pending approval.")
    service.record_req_event(req_id, ident[0], "checklist_approved",
                             f"Approved checklist v{version}")
    return {"ok": True, "approval": record}


# --- candidate batch ---------------------------------------------------------------


# Résumé preparation (2026-08-26) runs in a detached background worker: uploads
# stage bytes and return instantly; the worker splits bulk PDFs, de-duplicates
# and indexes, updating a state file the tab polls. See batch_prep.py — the
# per-request splitting this replaced took 15-30 minutes of in-request work for
# a real 6-packet iCIMS upload on the hosted single-vCPU plan.
def _prep_payload(req: Requisition, folder: Path, actor: str = "") -> dict:
    """The batch's preparation state, reconciled — a dead worker is re-spawned or
    finalized here, so ordinary polling self-heals a crash."""
    from resume_screener import batch_prep

    return batch_prep.checked(folder, req.req_id, actor=actor,
                              region=service.region_of_req(req))


def _prep_live_409(prep: dict) -> None:
    """409 for batch edits while the preparation worker is actually running —
    it owns the folder. (Adding MORE files stays allowed; the worker drains
    whatever is staged.)"""
    if prep.get("live"):
        raise HTTPException(409, "Résumés are still being prepared for this role — "
                                 "removing or clearing waits until that finishes.")


def _ack_sentence(prep: dict, staged: int, unsupported: int) -> str:
    """The upload response's one-liner: the final counts when preparation already
    finished (tiny batches, tests), else an instant acknowledgement."""
    if not prep.get("active") and prep.get("sentence"):
        sentence = str(prep["sentence"])
    else:
        sentence = f"{staged} file{'s' if staged != 1 else ''} received — preparing résumés…"
    if unsupported:
        sentence += f" · {unsupported} unsupported skipped"
    return sentence


@app.get("/api/roles/{req_id}/batch")
def get_batch(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    from resume_screener import resume_split
    from resume_screener.ats.local_folder import batch_files

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    folder = service.batch_folder(cfg, req)
    prep = _prep_payload(req, folder, actor=ident[0])
    files = [{"name": p.name} for p in batch_files(folder)]
    return {"count": len(files), "files": files, "folder": str(folder),
            "groups": resume_split.batch_groups(folder), "preparing": prep}


@app.post("/api/roles/{req_id}/batch/upload")
def upload_batch(req_id: str, files: list[UploadFile], auto_screen: bool = False,
                 ident: tuple[str, str] = Depends(_recruiter),
                 _rl: None = Depends(ratelimit.limited("upload"))) -> dict:
    """Accept résumés into one requisition's batch — instantly. The request only
    STAGES bytes (no hashing, no parsing); the preparation worker does the rest in
    the background and get_batch's `preparing` block narrates its progress. Still
    a SYNC def so the spooled-file reads happen off the event loop."""
    from resume_screener import batch_prep

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _batch_locked_409(req_id)
    _manual_lock_409(req, "candidates")
    folder = service.batch_folder(cfg, req)
    staged = unsupported = 0
    for upload in files:
        data = upload.file.read()  # sync read of the spooled temp file (we're off the loop)
        if batch_prep.stage_bytes(upload.filename or "resume", data, folder) == "staged":
            staged += 1
        else:
            unsupported += 1
    # "Upload and screen" is recorded SERVER-side before the worker is reconciled, so
    # closing the tab cannot lose it — the recruiter dropped the files to walk away.
    if auto_screen and staged:
        batch_prep.set_auto_screen(folder, actor=ident[0])
    body = get_batch(req_id, ident)  # reconciles: spawns/continues the worker
    return {"sentence": _ack_sentence(body["preparing"], staged, unsupported), **body}


@app.post("/api/roles/{req_id}/batch/import")
def import_batch(req_id: str, payload: dict = Body(...),
                 ident: tuple[str, str] = Depends(_recruiter),
                 _rl: None = Depends(ratelimit.limited("upload"))) -> dict:
    """Copy résumés from the legacy shared folder (cfg.ats.resumes_dir) into this
    requisition's batch — and from nowhere else (batch_prep.stage_folder)."""
    from resume_screener import batch_prep
    from resume_screener.ats.local_folder import BatchError

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _batch_locked_409(req_id)
    _manual_lock_409(req, "candidates")
    folder = service.batch_folder(cfg, req)
    source = str(payload.get("folder", "")).strip()
    if not source:
        raise HTTPException(400, "Give a folder path to import from.")
    try:
        staged, unsupported = batch_prep.stage_folder(source, folder,
                                                      within=cfg.ats.resumes_dir)
    except BatchError as exc:
        raise HTTPException(400, str(exc))
    body = get_batch(req_id, ident)
    return {"sentence": _ack_sentence(body["preparing"], staged, unsupported), **body}


@app.post("/api/roles/{req_id}/batch/prepare/stop")
def stop_prepare(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Stop preparing résumés. Cooperative: the worker finishes the file it is on,
    discards what is still queued, and keeps every candidate already added — so the
    batch is never left half-written."""
    from resume_screener import batch_prep

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _manual_lock_409(req, "candidates")
    folder = service.batch_folder(cfg, req)
    batch_prep.request_stop(folder, req_id)
    service.record_req_event(req_id, ident[0], "batch_prep_stopped",
                             "Stopped preparing résumés")
    return get_batch(req_id, ident)


@app.post("/api/roles/{req_id}/batch/prepare")
def retry_prepare(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """The preparing card's "Try again": clear the attempt cap and reconcile, so a
    fixed environment picks the staged files back up."""
    from resume_screener import batch_prep

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _batch_locked_409(req_id)
    _manual_lock_409(req, "candidates")
    folder = service.batch_folder(cfg, req)
    batch_prep.reconcile(folder, req.req_id, actor=ident[0],
                         region=service.region_of_req(req), reset_attempts=True)
    return get_batch(req_id, ident)


@app.get("/api/roles/{req_id}/batch/file/{filename}")
def download_batch_file(req_id: str, filename: str, inline: bool = False,
                        ident: tuple[str, str] = Depends(_recruiter_or_read)):  # noqa: ANN201
    """Download ONE résumé straight from the batch — the pre-screening download
    (the candidate_id/private-map bridge only exists after a run). Basename-only,
    parent-verified: no path traversal."""
    cfg = service.config()
    req = _req_authorized(req_id, ident)
    folder = service.batch_folder(cfg, req)
    target = folder / Path(filename).name  # basename only — no traversal
    if not (target.exists() and target.is_file() and target.parent.resolve() == folder.resolve()):
        raise HTTPException(404, "That résumé isn't in this role's batch.")
    return _resume_response(target, inline, req_id)


def _content_disposition(kind: str, filename: str) -> str:
    """The header Starlette's FileResponse would write (filename* for non-ASCII)."""
    from urllib.parse import quote

    quoted = quote(filename)
    return (f"{kind}; filename*=utf-8''{quoted}" if quoted != filename
            else f'{kind}; filename="{filename}"')


def _resume_response(path: Path, inline: bool, req_id: str):  # noqa: ANN202
    """A stored résumé as it leaves the server — never with active content in it
    (pdfsafe, 2026-10-16, pen-test RS-04). Intake cleans every PDF; this cleans
    again for anything stored before that or put in a batch by hand, and a PDF that
    can't be checked (unreadable yet naming JavaScript, or over the size cap) is
    only ever an attachment, never rendered in the browser."""
    from resume_screener import pdfsafe

    kind = "inline" if inline else "attachment"
    if path.suffix.lower() != ".pdf":
        return FileResponse(str(path), filename=path.name, content_disposition_type=kind)
    check = pdfsafe.checked_file(path)
    if check.attachment_only:
        kind = "attachment"
    if check.sanitized is None:
        return FileResponse(str(path), filename=path.name, content_disposition_type=kind)
    log.warning("Served a cleaned copy of %s/%s (removed: %s)", req_id, path.name,
                ", ".join(check.removed))
    return Response(check.sanitized, media_type="application/pdf",
                    headers={"Content-Disposition": _content_disposition(kind, path.name)})


@app.get("/api/roles/{req_id}/batch/download")
def download_batch_zip(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read),  # noqa: ANN201
                       _rl: None = Depends(ratelimit.limited("export"))):
    """Every résumé in the batch as one ZIP. Zipped to a temp file (streams to
    disk, not memory, so a 500-résumé batch is fine) and cleaned up after send."""
    import tempfile
    import zipfile

    from starlette.background import BackgroundTask

    from resume_screener.ats.local_folder import batch_files

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    folder = service.batch_folder(cfg, req)
    files = batch_files(folder)
    if not files:
        raise HTTPException(404, "This role's batch is empty — nothing to download.")

    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    seen: dict[str, int] = {}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            # De-dupe names inside the archive (batch already dedupes by content,
            # but two different files could share a basename after a split).
            name = p.name
            if name in seen:
                seen[name] += 1
                name = f"{p.stem} ({seen[p.name]}){p.suffix}"
            else:
                seen[name] = 0
            clean = _clean_copy(p)
            if clean is not None:
                zf.writestr(name, clean)
            else:
                zf.write(str(p), arcname=name)

    zip_name = f"{service.display_title(req) or req.req_id}-resumes.zip".replace("/", "-")
    return FileResponse(tmp, filename=zip_name, media_type="application/zip",
                        background=BackgroundTask(os.remove, tmp))


# --- Shortlist export (CSV + résumés-by-decision ZIP) --------------------------------
#
# One decision-aware export, shared by the standalone CSV and the résumé ZIP so
# the two never drift: the "Resume file" column in the CSV is exactly the path
# each résumé takes inside the ZIP (bucket folder + filename). NEVER used blind —
# this is the recruiter's Shortlist tab, which always shows real names.

# Decided bucket -> the folder a candidate's résumé lands in (None = not yet
# decided; it still gets a folder so "Undecided" is downloadable too).
_SHORTLIST_BUCKET_FOLDER = {
    "forward": "Move forward", "discuss": "Discuss", "not": "Not a fit",
}
_SHORTLIST_CSV_HEADER = [
    "iCIMS Req ID", "Rank", "Candidate", "Band", "Score", "Confidence",
    "Decision", "Decision note", "Noted by", "Decided on", "Gates not met",
    "Must-haves missing", "Top strengths", "Watch-outs", "Hiring manager take",
    "Resume file",
]
# The columns that are the team's decisions, and the ZIP layout they sort résumés
# into — left out for a viewer outside the requisition's team (2026-10-21).
_SHORTLIST_DECISION_COLUMNS = frozenset({
    "Decision", "Decision note", "Noted by", "Decided on", "Hiring manager take",
    "Resume file",
})
# The columns that ARE the AI's verdict — left out of the file entirely for a viewer
# without verdict_view, rather than shipped empty.
_SHORTLIST_VERDICT_COLUMNS = frozenset({
    "Band", "Score", "Confidence", "Gates not met", "Must-haves missing",
    "Top strengths", "Watch-outs",
})


def _shortlist_plan(req: Requisition, candidate_ids: list[str] | None,
                    caps: frozenset[str] | set[str],
                    sees_decisions: bool) -> tuple[dict, list[dict]]:
    """(review payload, per-candidate export plan). Each plan item carries the
    review card, its decision bucket folder, the résumé source Path (or None when
    the file is gone), and the arcname it takes inside the ZIP — the same string
    the CSV's "Resume file" column shows. `candidate_ids` None = every candidate.
    The payload is the one this viewer may see: without verdict_view the cards carry
    no band, score or evidence, so neither the CSV nor the files' properties can."""
    data = verdict.review_for(
        decision_view.review_for(review.build_review(req, blind=False), sees_decisions), caps)
    want = {c for c in candidate_ids} if candidate_ids is not None else None
    seen: dict[str, dict[str, int]] = {}  # bucket -> basename -> count (de-dupe)
    plan: list[dict] = []
    for card in data.get("cards") or []:
        cid = card["candidate_id"]
        if want is not None and cid not in want:
            continue
        bucket = _SHORTLIST_BUCKET_FOLDER.get(card.get("decided") or "", "Undecided")
        src = service.resume_path(req.req_id, cid)
        arcname = ""
        if src is not None:
            names = seen.setdefault(bucket, {})
            # The filename CARRIES the decision record (2026-09-07): a résumé that
            # leaves this app arrives somewhere with no database behind it, so who
            # decided what, against which checklist, has to travel in the file
            # itself. Name first (it survives every tool), document properties
            # second (below, best-effort).
            name = _shortlist_filename(src, card, data.get("req_code") or "")
            if name in names:
                names[name] += 1
                stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
                name = f"{stem} ({names[name]}).{suffix}" if suffix else f"{stem} ({names[name]})"
            else:
                names[name] = 0
            arcname = f"{bucket}/{name}"
        plan.append({"card": card, "bucket": bucket, "src": src, "arcname": arcname})
    return data, plan


def _safe_component(text: str, limit: int = 60) -> str:
    """A filename-safe fragment: no separators, no reserved characters, trimmed."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text)).strip().rstrip(".")
    return cleaned[:limit].strip() or "unknown"


def _shortlist_filename(src: Path, card: dict, req_code: str) -> str:
    """`Anita Rao — REQ-2041 — checklist v4 — Move forward — 2026-09-07.pdf`.
    Every part is already on the card; a missing part is simply left out."""
    parts = [_safe_component(card.get("name") or src.stem)]
    if req_code:
        parts.append(_safe_component(req_code, 30))
    decided = card.get("decided")
    if decided:
        parts.append(_safe_component(service.ACTION_LABEL.get(decided, decided), 24))
    ts = str(card.get("decided_ts") or "")[:10]
    if ts:
        parts.append(ts)
    return " — ".join(parts) + src.suffix


def _clean_copy(path: Path) -> bytes | None:
    """A stored PDF's cleaned bytes when it carries active content, else None (ship
    the file itself). For the ZIPs, so a download is as clean as the viewer."""
    from resume_screener import pdfsafe

    if path.suffix.lower() != ".pdf":
        return None
    try:
        return pdfsafe.checked_file(path).sanitized
    except OSError:
        return None


def _stamp_resume_metadata(src: Path, card: dict, meta: dict) -> bytes | None:
    """The same decision record written into the FILE's own properties — PDF
    document info, DOCX core properties. Best-effort by design: any parse or write
    problem returns None and the caller ships the untouched original, because a
    résumé that opens is worth more than one that is labelled."""
    subject = (f"{meta.get('req_code', '')} · checklist {meta.get('checklist', '')} · "
               f"{meta.get('decision', '')}").strip(" ·")
    comments = "; ".join(x for x in [
        f"Requisition: {meta.get('req_code', '')}" if meta.get("req_code") else "",
        f"Checklist: {meta.get('checklist', '')}" if meta.get("checklist") else "",
        f"Decision: {meta.get('decision', '')}" if meta.get("decision") else "",
        f"Decided by: {meta.get('decided_by', '')}" if meta.get("decided_by") else "",
        f"Decided: {meta.get('decided_ts', '')}" if meta.get("decided_ts") else "",
        # Only when the viewer may see the verdict — the card carries none otherwise.
        f"Match: {card.get('band_label', '')} ({card.get('score', '')}/100)"
        if card.get("band_label") else "",
        "Assistive screening — decision-support only, recorded by a human reviewer.",
    ] if x)
    try:
        if src.suffix.lower() == ".pdf":
            import io

            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(str(src))
            if reader.is_encrypted:  # never fight a protected file
                return None
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            writer.add_metadata({k: v for k, v in {
                **{k: v for k, v in (reader.metadata or {}).items()},
                "/Title": _safe_component(card.get("name") or src.stem),
                "/Subject": subject,
                "/Keywords": comments,
            }.items() if isinstance(k, str) and k.startswith("/")})
            buf = io.BytesIO()
            writer.write(buf)
            from resume_screener import pdfsafe

            return pdfsafe.strip(buf.getvalue())[0]  # add_page carries page actions
        if src.suffix.lower() == ".docx":
            import io

            from docx import Document

            doc = Document(str(src))
            props = doc.core_properties
            props.title = _safe_component(card.get("name") or src.stem)
            props.subject = subject
            props.comments = comments
            buf = io.BytesIO()
            doc.save(buf)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 — labelling must never cost the file itself
        return None
    return None


def _shortlist_csv(data: dict, plan: list[dict], show_verdict: bool = True,
                   show_decisions: bool = True) -> str:
    """The TA-actionable CSV: identity + fit + why + decision + where the résumé
    sits in the download. One row per included candidate. Without the verdict
    (show_verdict False) the fit/why columns are not written at all; outside the
    requisition's team (show_decisions False) neither are the decision columns."""
    import csv
    import io

    code = data.get("req_code") or ""
    buf = io.StringIO()
    writer = csv.writer(buf)
    keep = [i for i, col in enumerate(_SHORTLIST_CSV_HEADER)
            if (show_verdict or col not in _SHORTLIST_VERDICT_COLUMNS)
            and (show_decisions or col not in _SHORTLIST_DECISION_COLUMNS)]
    writer.writerow([_SHORTLIST_CSV_HEADER[i] for i in keep])
    for item in plan:
        c = item["card"]
        chips = c.get("chips") or []
        strengths = [h["text"] for h in chips if h.get("kind") == "strength"]
        watchouts = [h["text"] for h in chips if h.get("kind") == "watchout"]
        decided = c.get("decided")
        hm = c.get("hm_take")
        ts = c.get("decided_ts") or ""
        row = [
            code,
            c.get("rank", ""),
            c.get("name", ""),
            c.get("band_label", ""),
            c.get("score", ""),
            c.get("confidence", ""),
            service.ACTION_LABEL.get(decided, "") if decided else "",
            c.get("decided_reason") or "",
            c.get("decided_by") or "",
            ts[:10],  # ISO date portion; locale-independent
            "; ".join((c.get("gates") or {}).get("failed") or []),
            "; ".join(c.get("must_have_gaps") or []),
            "; ".join(strengths),
            "; ".join(watchouts),
            (f'{hm["take"]}' + (f' ({hm["hm_name"]})' if hm.get("hm_name") else "")) if hm else "",
            item["arcname"],
        ]
        writer.writerow([row[i] for i in keep])
    return buf.getvalue()


@app.get("/api/roles/{req_id}/shortlist/export.csv")
def export_shortlist_csv(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read),  # noqa: ANN201
                         _rl: None = Depends(ratelimit.limited("export"))):
    """The whole shortlist as one CSV (every candidate). The "Resume file" column
    is the path each résumé takes in the download ZIP — so the CSV and the ZIP
    describe the same layout."""
    req = _req_authorized(req_id, ident)
    caps = _request_caps()
    sees = _sees_decisions(req, ident[0], caps)
    data, plan = _shortlist_plan(req, None, caps, sees)
    if not data.get("ready"):
        raise HTTPException(400, "This role hasn't been screened yet.")
    csv_text = _shortlist_csv(data, plan, show_verdict=verdict.allowed(caps),
                              show_decisions=sees)
    name = f"shortlist-{_safe_component(data.get('req_code') or req.req_id)}.csv"
    return Response(csv_text, media_type="text/csv",
                    headers={"Content-Disposition": _content_disposition("attachment", name)})


@app.post("/api/roles/{req_id}/shortlist/download")
def download_shortlist_zip(req_id: str, payload: dict = Body(default={}),
                           ident: tuple[str, str] = Depends(_recruiter_or_read),  # noqa: ANN201
                           _rl: None = Depends(ratelimit.limited("export"))):
    """The selected candidates' résumés as one structured ZIP: a folder per
    decision (Move forward / Discuss / Not a fit / Undecided), plus the shortlist
    CSV at the top level. Selection-driven — the whole-shortlist CSV is the
    export.csv route. Zipped to a temp file (streams to disk) and cleaned up after
    send — same as the batch ZIP."""
    import tempfile
    import zipfile

    from starlette.background import BackgroundTask

    req = _req_authorized(req_id, ident)
    caps = _request_caps()
    # Folders per decision, the decision in every filename: the whole file IS the
    # team's decisions, so it is theirs alone (2026-10-21).
    if not _sees_decisions(req, ident[0], caps):
        raise HTTPException(403, decision_view.HIDDEN_MESSAGE
                            + " The shortlist download sorts résumés by those decisions.")
    ids = [str(c).strip() for c in (payload.get("candidate_ids") or []) if str(c).strip()]
    if not ids:
        raise HTTPException(400, "Select at least one candidate to download.")
    data, plan = _shortlist_plan(req, ids, caps, True)
    if not data.get("ready"):
        raise HTTPException(400, "This role hasn't been screened yet.")
    if not plan:
        raise HTTPException(400, "None of the selected candidates are in this shortlist.")

    code = data.get("req_code") or req.req_id
    csv_text = _shortlist_csv(data, plan, show_verdict=verdict.allowed(caps))
    csv_name = f"shortlist-{code}.csv".replace("/", "-")

    # The checklist version these decisions were taken against — part of every
    # file's record, so a résumé read months later says what it was judged by.
    m = re.search(r"\(v(\d+)\)", str((data.get("meta") or {}).get("rubric") or ""))
    checklist = f"v{m.group(1)}" if m else ""

    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, csv_text)  # the export file, at the top level
        for item in plan:
            if item["src"] is None:
                continue
            card = item["card"]
            decided = card.get("decided")
            stamped = _stamp_resume_metadata(item["src"], card, {
                "req_code": code,
                "checklist": checklist,
                "decision": service.ACTION_LABEL.get(decided, "") if decided else "No decision",
                "decided_by": card.get("decided_by") or "",
                "decided_ts": str(card.get("decided_ts") or "")[:19],
            })
            if stamped is not None:
                zf.writestr(item["arcname"], stamped)
            elif (clean := _clean_copy(item["src"])) is not None:
                zf.writestr(item["arcname"], clean)
            else:
                zf.write(str(item["src"]), arcname=item["arcname"])

    zip_name = f"shortlist-{code}.zip".replace("/", "-")
    return FileResponse(tmp, filename=zip_name, media_type="application/zip",
                        background=BackgroundTask(os.remove, tmp))


@app.delete("/api/roles/{req_id}/batch/{filename}")
def remove_from_batch(req_id: str, filename: str,
                      ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _batch_locked_409(req_id)
    _manual_lock_409(req, "candidates")
    folder = service.batch_folder(cfg, req)
    _prep_live_409(_prep_payload(req, folder, actor=ident[0]))
    target = folder / Path(filename).name  # basename only — no traversal
    if target.exists() and target.parent.resolve() == folder.resolve():
        target.unlink()
    # Keep the split manifest + stored originals honest after a removal.
    from resume_screener import resume_split
    resume_split.cleanup_removed(folder)
    return get_batch(req_id, ident)


@app.delete("/api/roles/{req_id}/batch")
def clear_batch_route(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    from resume_screener import resume_split
    from resume_screener.ats.local_folder import clear_batch

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _batch_locked_409(req_id)
    _manual_lock_409(req, "candidates")
    folder = service.batch_folder(cfg, req)
    from resume_screener import batch_prep

    # Clearing while preparation is LIVE would race the worker — it could write a
    # candidate file moments after the folder was emptied. So ask it to stop, give it
    # a moment to notice, and only then clear. Still live after that? Say so plainly
    # rather than clearing into a moving target.
    if _prep_payload(req, folder, actor=ident[0]).get("live"):
        batch_prep.request_stop(folder, req_id)
        for _ in range(12):
            time.sleep(0.5)
            if not batch_prep.session_payload(folder).get("live"):
                break
        else:
            raise HTTPException(409, "Résumés are still being prepared — it is stopping "
                                     "now. Try clearing again in a moment.")
    batch_prep.clear_prep_files(folder)  # staged uploads + prep state/index go too
    removed = clear_batch(folder)
    # Clearing the batch also clears any split sources + manifest (derived are gone).
    resume_split.cleanup_removed(folder)
    return {"removed": removed, **get_batch(req_id, ident)}


# --- screening ---------------------------------------------------------------------


def _controls_run(job: dict, user: str) -> bool:
    """Only the run's initiator — or a Super Admin — may pause, cancel or resume
    it (2026-07-19). An unknown initiator (e.g. a paused run surviving a restart
    on the DB backend, where the in-process actor is gone) falls open to any
    recruiter rather than locking everyone out of their own requisition."""
    owner = str(job.get("actor") or "").strip().lower()
    if not owner or owner == user.strip().lower():
        return True
    caps = _REQUEST_CAPS.get()
    return bool(caps) and "access_view" in caps  # the SuperAdmin-only capability


def _batch_locked_409(req_id: str) -> None:
    """409 for batch mutations while a run is live or paused — the run's résumé
    set must not change underneath it (applies to EVERYONE, initiator included;
    cancel the run first)."""
    lock = jobs.screen_lock(req_id)
    if lock is None:
        return
    owner = lock.get("actor") or "another user"
    state = "paused" if lock.get("status") == "paused" else "in progress"
    raise HTTPException(409, f"Screening is {state} (started by {owner}) — the "
                             "résumé batch is locked until the run finishes or is cancelled.")


_LOCK_TARGETS = ("checklist", "candidates")
_LOCK_NOUN = {"checklist": "checklist", "candidates": "candidate list"}


def _manual_lock_409(req, target: str) -> None:
    """409 when a target (checklist|candidates) is manually LOCKED (2026-07-20).
    A plain message names who locked it and who can unlock."""
    lk = service.req_locks(req).get(target)
    if not lk:
        return
    who = lk.get("locked_by_name") or lk.get("locked_by") or "another user"
    raise HTTPException(409, {
        "code": "manually_locked", "target": target, "locked_by": lk.get("locked_by"),
        "message": (f"This {_LOCK_NOUN[target]} is locked (by {who}). Unlock it to make "
                    "changes — only the person who locked it, an HR Head or a Super Admin can.")})


def _open_req_or_409(req) -> None:
    """409 when the requisition is closed (2026-10-16, pen-test RS-06). Closing
    used to change only a label: screening, decisions, uploads, checklist publishes
    and hiring-manager sharing all carried on. The hiring-work routes call this
    right after the access check — before the batch and manual-lock 409s and the
    staleness gate, because closed is the broadest reason and the others would send
    people to a button this one refuses. Reading, comments, collaborators, the
    iCIMS details, stopping work and deleting files stay open: closing freezes the
    hiring, not the record."""
    if not service.is_closed(req):
        return
    from resume_screener import feature_flags

    life = service.req_lifecycle(req)
    if feature_flags.flag_enabled("icims_reopen", service.region_of_req(req)):
        tail = "Reopen it from History on the Requisitions page to continue."
    else:
        tail = "Reopening is switched off — ask IT if it needs reopening."
    raise HTTPException(409, {
        "code": "closed", "closed_at": life.get("closed_at"), "outcome": life.get("outcome"),
        "message": f"This requisition is closed, so it can't be changed. {tail}"})


def _can_unlock(lock: dict, user: str) -> bool:
    """Unlock is allowed for the LOCKER, an HR Head or a Super Admin (reqs_all)."""
    if str(lock.get("locked_by", "")).strip().lower() == user.strip().lower():
        return True
    caps = _REQUEST_CAPS.get() or set()
    return "reqs_all" in caps


@app.post("/api/roles/{req_id}/lock")
def lock_target_route(req_id: str, payload: dict = Body(...),
                      ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Manually lock a requisition's checklist or candidate list (2026-07-20) so
    edits are frozen until someone deliberately unlocks it."""
    from resume_screener import names
    from resume_screener.ats.local_folder import save_req_meta

    req = _req_authorized(req_id, ident)
    target = str(payload.get("target", "")).strip()
    if target not in _LOCK_TARGETS:
        raise HTTPException(400, "Lock target must be 'checklist' or 'candidates'.")
    req_m = service.req_meta(req)
    locks = dict(req_m.get("locks") or {})
    locks[target] = {"locked_by": ident[0],
                     "locked_by_name": names.display_name(ident[0], req_meta=req_m),
                     "locked_at": _now()}
    save_req_meta(req.jd_ref, locks=locks)
    service.record_req_event(req_id, ident[0], "lock_set",
                             f"Locked the {_LOCK_NOUN[target]}")
    return {"ok": True, "locks": service.req_locks(req)}


@app.post("/api/roles/{req_id}/unlock")
def unlock_target_route(req_id: str, payload: dict = Body(...),
                        ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Unlock a checklist/candidate list — allowed for the locker, an HR Head or
    a Super Admin only."""
    from resume_screener.ats.local_folder import save_req_meta

    req = _req_authorized(req_id, ident)
    target = str(payload.get("target", "")).strip()
    if target not in _LOCK_TARGETS:
        raise HTTPException(400, "Lock target must be 'checklist' or 'candidates'.")
    locks = dict(service.req_meta(req).get("locks") or {})
    lk = locks.get(target)
    if not lk:
        return {"ok": True, "locks": service.req_locks(req)}  # already unlocked
    if not _can_unlock(lk, ident[0]):
        raise HTTPException(403, "Only the person who locked this, an HR Head or a "
                                 "Super Admin can unlock it.")
    locks.pop(target, None)
    save_req_meta(req.jd_ref, locks=locks)
    service.record_req_event(req_id, ident[0], "lock_cleared",
                             f"Unlocked the {_LOCK_NOUN[target]}")
    return {"ok": True, "locks": service.req_locks(req)}


@app.get("/api/roles/{req_id}/screen/preflight")
def screen_preflight(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    """What would screening do right now? Read-only change detection (P35) —
    the Candidates tab tells the truth about cache reuse before any run."""
    cfg = service.config()
    req = _req_authorized(req_id, ident)
    return service.screen_preflight(cfg, req)


@app.post("/api/roles/{req_id}/screen")
def start_screen(req_id: str, payload: dict = Body(default={}),
                 ident: tuple[str, str] = Depends(_recruiter),
                 _rl: None = Depends(ratelimit.limited("screen"))) -> dict:
    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    # A blank JD means there's nothing to screen against — block it server-side
    # too (the button is disabled client-side, but the engine must never run on
    # an empty description).
    if not service.jd_present(req):
        raise HTTPException(400, "Add a job description for this role before screening.")
    rubric = service.rubric_for(req)
    if rubric is None:
        raise HTTPException(400, "Approve a screening checklist for this role first.")
    # P43 enforced server-side: a critic-flagged checklist can't screen until
    # the HR Head signs that exact version off (same source the editor banner reads).
    gate = service.checklist_gate(req, rubric)
    if not gate["ready"]:
        raise HTTPException(400, gate["reason"])
    if service.batch_count(cfg, req) == 0:
        raise HTTPException(400, "No candidates in this role's batch yet — add résumés first.")
    # Résumés still being prepared (splitting / duplicate checks) are not yet part
    # of the batch — a run started now would silently miss them. The Candidates
    # tab shows live progress; this is the server-side backstop.
    prep = _prep_payload(req, service.batch_folder(cfg, req), actor=ident[0])
    if prep.get("active"):
        raise HTTPException(409, "Résumés are still being prepared for this role — "
                                 "screening can start when they're ready.")
    # One run per requisition (2026-07-19): while a run is live or paused, only
    # its initiator or a Super Admin may act — anyone else gets a plain 409
    # (starting while YOUR OWN run is live stays the idempotent no-op it was).
    lock = jobs.screen_lock(req_id)
    if lock is not None and not _controls_run(lock, ident[0]):
        owner = lock.get("actor") or "another user"
        state = "paused" if lock.get("status") == "paused" else "already running"
        raise HTTPException(409, f"Screening is {state} — started by {owner}. "
                                 "Only they or a Super Admin can "
                                 + ("resume or cancel it." if state == "paused"
                                    else "pause or cancel it."))
    job = jobs.start_screening(
        req_id, req.jd_ref,
        jurisdiction=(payload or {}).get("jurisdiction"),
        limit=(payload or {}).get("limit"),
        concurrency=(payload or {}).get("concurrency"),
        actor=ident[0],  # stamped onto the screen_completed event (P29)
    )
    return job


@app.get("/api/screen/jobs/{job_id}")
def screen_status(job_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """A run's progress — for someone who may see its requisition (2026-10-16): it
    names the requisition, who started the run and the files done so far."""
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "No such screening job.")
    _req_authorized(job["req_id"], ident)
    return job


@app.post("/api/screen/jobs/{job_id}/cancel")
def screen_cancel(job_id: str, payload: dict = Body(default={}),
                  ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Pause or cancel a screening run (2026-07-19 split). Both kill the
    subprocess — the finished per-candidate work is cached either way; killing
    mid-model-call would only poison the run. PAUSE keeps the batch locked (the
    ordinary screen click resumes from cache); CANCEL unlocks the batch so
    résumés can change and the next screen starts fresh. Only the initiator or
    a Super Admin may do either."""
    intent = str((payload or {}).get("intent", "cancel")).strip() or "cancel"
    if intent not in ("pause", "cancel"):
        raise HTTPException(400, "Intent must be 'pause' or 'cancel'.")
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "No such screening job.")
    _req_authorized(job["req_id"], ident)  # region lens applies to control too
    if not _controls_run(job, ident[0]):
        owner = job.get("actor") or "the person who started it"
        raise HTTPException(403, f"Only {owner} or a Super Admin can "
                                 f"{'pause' if intent == 'pause' else 'cancel'} this run.")
    outcome, job = jobs.cancel_job(job_id, ident[0], intent=intent)
    if outcome == "not_found":
        raise HTTPException(404, "No such screening job.")
    if outcome == "not_running":
        raise HTTPException(409, "This screening run already finished.")
    if outcome == "not_here":
        raise HTTPException(409, "This screening run can't be stopped from here — "
                                 "it was started by another app instance.")
    return job


@app.get("/api/roles/{req_id}/screen/runs")
def screen_runs(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    """The persistent screening-run history for this requisition (who ran it,
    when, and how it ended), newest first — the Candidates tab's run log."""
    req = _req_authorized(req_id, ident)
    return verdict.runs_for(service.screen_runs(req), _request_caps())


@app.get("/api/roles/{req_id}/screen/active")
def screen_active(req_id: str, ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    """The in-flight screening job for this requisition (`job`), plus the most
    recent run when none is live (`last`). Lets the Candidates tab re-attach its
    progress bar after the user navigated away — and re-show a run that STOPPED
    or FAILED so its panel (and which résumés it screened) survives a reload or a
    user switch, not just a bare CTA. The run never depended on the UI staying
    open."""
    _req_authorized(req_id, ident)
    running = jobs.active_job_for(req_id)
    return {"job": running, "last": None if running else jobs.latest_job_for(req_id)}


@app.get("/api/screen/active")
def screen_active_all(ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Every in-flight screening job across all requisitions, so the Requisitions
    list can show a live "Screening N/M" in the Candidates column while the user
    is off on another req (a recruiter juggling 8–9 reqs shouldn't lose track of
    which batch finished first). One poll for the whole dashboard; read-only and
    PII-free — just req_id + progress counts. Own-reqs-scoped recruiters
    (2026-07-19) only see jobs on requisitions they could open."""
    caps = _REQUEST_CAPS.get()
    live = jobs.active_jobs()
    if caps is not None and "reqs_all" not in caps:
        visible = []
        for j in live:
            req = service.find_requisition(j.get("req_id", "") if isinstance(j, dict)
                                           else getattr(j, "req_id", ""))
            if req is None or not _own_req_denied(req, ident[0], caps):
                visible.append(j)
        live = visible
    return {"jobs": live}


# ============================ REVIEW ============================


def _is_hm_viewer(caps: list[str]) -> bool:
    """The hiring-manager lens: holds hm_take but NOT recruiter (a TA who is
    also an HM keeps the full recruiter view — mirrors web/src/hm.ts).
    Takes the viewer_identity CAPABILITY list (its third element)."""
    return "hm_take" in caps and "recruiter" not in caps


@app.get("/api/roles/{req_id}/review")
def get_review(req_id: str, blind: bool = False,
               vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    req = _req_view_authorized(req_id, vident[0], vident[2])
    payload = review.build_review(req, blind=blind, hm_view=_is_hm_viewer(vident[2]))
    payload = decision_view.review_for(payload, _sees_decisions(req, vident[0], vident[2]))
    return verdict.review_for(payload, vident[2], blind=blind)


@app.get("/api/roles/{req_id}/review/{candidate_id}")
def get_review_detail(req_id: str, candidate_id: str, blind: bool = False,
                      vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    req = _req_view_authorized(req_id, vident[0], vident[2])
    # An HM can open ONLY candidates on the shared shortlist — never reach an
    # unshared candidate by guessing its id.
    hm_view = _is_hm_viewer(vident[2])
    if hm_view:
        shared = review.build_review(req, hm_view=True)
        if candidate_id not in {c["candidate_id"] for c in shared.get("cards", [])}:
            raise HTTPException(403, "This candidate hasn't been shared with you yet.")
    detail = review.build_detail(req, candidate_id, blind=blind, viewer=vident[0],
                                 hm_view=hm_view)
    if detail is None:
        raise HTTPException(404, "No such candidate in this run.")
    detail = decision_view.detail_for(detail, _sees_decisions(req, vident[0], vident[2]))
    return verdict.detail_for(detail, vident[2])


def _notify_comment(req, req_m: dict, hm: dict, candidate_id: str, record: dict,
                    author_email: str, hm_view: bool, author_name: str) -> None:
    """Live comment fan-out (2026-07-20): notify the OTHER party immediately and
    any @-mentioned people. HM comment → the requisition owner; recruiter comment
    → the assigned HM (only for a candidate already shared). Everyone is notified
    at most once — an @mention wins over the generic 'commented' notice."""
    from resume_screener.notify import notify

    title = service.display_title(req)
    cid_data = {"candidate_id": candidate_id, "comment_id": record["comment_id"]}
    done: set[str] = {author_email.strip().lower()}

    def send(email: str | None, kind: str, msg: str) -> None:
        email = str(email or "").strip()
        if not email or email.lower() in done:
            return
        done.add(email.lower())
        notify(email, msg, actor=author_email, req_id=req.req_id,
               data={"kind": kind, **cid_data})

    for email in record.get("mentions") or []:   # @mentions are the sharper signal
        send(email, "comment_mention", f"{title}: {author_name} mentioned you in a comment.")
    _email_outside_mentions(req, record, author_email, author_name, candidate_id)
    msg = f"{title}: {author_name} commented on a candidate."
    if hm_view:
        send(req_m.get("created_by"), "comment_added", msg)
    else:
        hm_email = str(hm.get("email") or "").strip()
        share = service.hm_share_record(req.req_id)
        shared = {row.get("candidate_id") for row in (share or {}).get("snapshot") or []}
        if hm_email and candidate_id in shared:  # only ping the HM about a shared candidate
            send(hm_email, "comment_added", msg)


def _email_outside_mentions(req, record: dict, author_email: str, author_name: str,
                            candidate_id: str) -> None:
    """An @-mention of someone who has never signed in reaches them by EMAIL
    (2026-09-06) — the comment, the requisition (title + iCIMS Req ID), the
    candidate's name and a link back into the app, so a non-user can answer by
    replying or by opening the app. People who use the app already get the bell;
    they are not emailed twice. Delivery is the mailer's seam: sent through Graph
    when the deployment has a sending mailbox, queued to signals/outbox.jsonl
    otherwise — never dropped, always on the event spine. The résumé travels as an
    attachment only when the `mention_email_attach_resume` flag is on."""
    from resume_screener import feature_flags, mailer, people

    outside = [m for m in (record.get("mentions") or [])
               if (people.lookup(m) or {}).get("source") != "sso"
               and m.strip().lower() != author_email.strip().lower()]
    if not outside:
        return
    title = service.display_title(req)
    code = service.icims_of(req) or ""
    cand = service.candidate_name(req.req_id, candidate_id)
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    link = f"{base}/reqs/{req.req_id}/review?focus=comment:{candidate_id}:{record['comment_id']}"
    subject = (f"{author_name} wants your opinion on a candidate — {title}"
               + (f" ({code})" if code else ""))
    where = f'"{title}"' + (f" (iCIMS Req ID {code})" if code else "")
    body = "\n".join([
        f"{author_name} mentioned you in Resume Screener on the requisition {where}, "
        f"about the candidate {cand}:",
        "",
        f"    {record['text']}",
        "",
        "You can reply to this email, or open the candidate in Resume Screener:",
        f"    {link}",
        "",
        "Resume Screener is decision support for Talent Acquisition — it suggests, people decide.",
    ])
    attachments: list[dict] = []
    if feature_flags.flag_enabled("mention_email_attach_resume", service.region_of_req(req)):
        try:
            path = service.resume_path(req.req_id, candidate_id)
            if path is not None and path.exists():
                attachments.append({
                    "name": path.name, "bytes": path.read_bytes(),
                    "content_type": ("application/pdf" if path.suffix.lower() == ".pdf"
                                     else "application/octet-stream")})
        except Exception:  # noqa: BLE001 — the email still goes; the file is a courtesy
            pass
    for email in outside:
        mailer.send(email, subject, body, actor=author_email, req_id=req.req_id,
                    kind="comment_mention_email", attachments=attachments,
                    data={"candidate_id": candidate_id, "comment_id": record["comment_id"]})


@app.post("/api/roles/{req_id}/candidates/{candidate_id}/comments")
def add_candidate_comment(req_id: str, candidate_id: str, payload: dict = Body(...),
                          vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """Add a comment to a candidate's collaboration thread. Open to the recruiting
    side and the ASSIGNED hiring manager (portal-gated, and only on candidates
    already shared with them). Comments are LIVE both ways (2026-07-20): every
    comment notifies the OTHER party immediately and appears in their thread on
    the next poll — no 'send updates' click. @mentions notify the people named."""
    from resume_screener import comments as comments_mod, names
    from resume_screener.notify import notify

    req = _req_view_authorized(req_id, vident[0], vident[2])
    _deny_read_only(vident[2])  # read-only viewers can't post comments
    req_m = service.req_meta(req)
    hm_view = _is_hm_viewer(vident[2])
    hm = req_m.get("hm") or {}
    member_ids: set[str] = set()
    if hm_view:
        _hm_portal_or_403()
        shared = review.build_review(req, hm_view=True)
        member_ids = {c["candidate_id"] for c in shared.get("cards", [])}
        if candidate_id not in member_ids:
            raise HTTPException(403, "This candidate hasn't been shared with you yet.")
    elif not any(r["score"]["candidate_id"] == candidate_id
                 for r in service.read_candidate_records(req_id)):
        raise HTTPException(404, "No such candidate in this run.")
    author_name = names.display_name(vident[0], req_meta=req_m,
                                     known=(str(hm.get("name") or "") if hm_view else ""))
    mentions = [str(m).strip() for m in (payload.get("mentions") or []) if str(m).strip()]
    try:
        record = comments_mod.add_comment(
            req_id, candidate_id, author=vident[0], text=str(payload.get("text", "")),
            author_name=author_name, mentions=mentions,
            author_role="Hiring manager" if hm_view else "Recruiting team",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    service.record_req_event(req_id, vident[0], "comment_added",
                             f"{author_name} commented on a candidate",
                             data={"candidate_id": candidate_id,
                                   "comment_id": record["comment_id"]})
    _notify_comment(req, req_m, hm, candidate_id, record, vident[0], hm_view, author_name)
    return {"ok": True, "comment": {
        "comment_id": record["comment_id"], "author": record["author"],
        "author_name": record["author_name"], "author_role": record["author_role"],
        "text": record["text"], "ts": record["ts"],
        "mentions": record["mentions"], "reactions": []}}


def _hm_candidate_or_403(req, candidate_id: str, caps: list[str]) -> None:
    """For an HM viewer, ensure the candidate is on their shared shortlist."""
    if not _is_hm_viewer(caps):
        return
    _hm_portal_or_403()
    shared = review.build_review(req, hm_view=True)
    if candidate_id not in {c["candidate_id"] for c in shared.get("cards", [])}:
        raise HTTPException(403, "This candidate hasn't been shared with you yet.")


@app.post("/api/roles/{req_id}/candidates/{candidate_id}/comments/{comment_id}/react")
def react_to_comment(req_id: str, candidate_id: str, comment_id: str,
                     payload: dict = Body(...),
                     vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """Toggle the signed-in user's emoji reaction on a comment (2026-07-20). Open
    to the recruiting side and the assigned HM (on shared candidates)."""
    from resume_screener import comments as comments_mod

    req = _req_view_authorized(req_id, vident[0], vident[2])
    _deny_read_only(vident[2])  # read-only viewers can't react
    _hm_candidate_or_403(req, candidate_id, vident[2])
    # The comment must be on THIS requisition and candidate: the id alone used to be
    # enough to react to any comment anywhere (2026-10-16).
    if not any(c.get("comment_id") == comment_id
               for c in comments_mod.list_comments(req_id, candidate_id)):
        raise HTTPException(404, "No such comment.")
    try:
        rmap = comments_mod.toggle_reaction(
            comment_id, user=vident[0], emoji=str(payload.get("emoji", "")).strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "comment_id": comment_id,
            "reactions": review.reaction_view(rmap, vident[0], service.req_meta(req))}


@app.get("/api/roles/{req_id}/participants")
def req_participants_route(req_id: str,
                           vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """People you can @-mention on this requisition (2026-07-20): the recruiting
    team + the assigned HM. The viewer is excluded (no self-mentions)."""
    req = _req_view_authorized(req_id, vident[0], vident[2])
    me = vident[0].strip().lower()
    return {"participants": [p for p in service.req_participants(req)
                             if p["email"].strip().lower() != me]}


@app.get("/api/roles/{req_id}/candidates/{candidate_id}/comments")
def get_candidate_comments(req_id: str, candidate_id: str,
                           vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """The live comment thread for one candidate (2026-07-20) — the drawer POLLS
    this so comments + reactions appear in near-real-time, without re-running the
    whole detail (score math etc.) each tick."""
    from resume_screener import comments as comments_mod

    req = _req_view_authorized(req_id, vident[0], vident[2])
    _hm_candidate_or_403(req, candidate_id, vident[2])
    thread = comments_mod.list_comments(req_id, candidate_id)
    rmap = comments_mod.reactions_by_comment([c["comment_id"] for c in thread])
    req_m = service.req_meta(req)
    return {"comments": [{"comment_id": c["comment_id"], "author": c["author"],
                          "author_name": c.get("author_name", ""),
                          "author_role": c.get("author_role", ""),
                          "text": c["text"], "ts": c["ts"],
                          "mentions": c.get("mentions", []),
                          "reactions": review.reaction_view(
                              rmap.get(c["comment_id"], {}), vident[0], req_m)}
                         for c in thread]}


@app.post("/api/roles/{req_id}/share-hm")
def share_with_hm(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Share the current run's shortlist with the requisition's hiring manager.
    Until this is clicked the HM's Review tab is EMPTY; a re-screen (new run_id)
    clears the share until it's clicked again.

    The share sets MEMBERSHIP — which candidates the HM may see. Within that set,
    decisions and comments are LIVE (2026-07-20, near-real-time polling), so the
    recruiter never has to 'send updates' for collaboration. Clicking again just
    EXTENDS membership to candidates scored since (so the unshared batch never
    leaks); if there's nothing new to add it's a friendly no-op."""
    from resume_screener import names
    from resume_screener.notify import notify

    cfg = service.config()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _team_or_403(req, ident)  # decisions are the team's (2026-10-21)
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(400, "Screen the candidates first — there is nothing to share yet.")
    req_m = service.req_meta(req)
    hm = req_m.get("hm") or {}
    hm_email = str(hm.get("email") or "").strip()
    if not hm_email:
        raise HTTPException(400, "Add a hiring manager (with an email) on the Overview tab first.")
    decisions = service.role_decisions(req_id)
    decided = [a for a in decisions.values() if a.get("action") in ("forward", "discuss", "not")]
    if not decided:
        raise HTTPException(400, "Record at least one decision before sharing — the hiring "
                                 "manager reviews your decided shortlist.")
    prior = service.hm_share_record(req_id, meta)
    updates = service.hm_share_updates(cfg, req, prior)
    if prior is not None and not updates["pending"]:
        # Comments + decisions are already live to the HM — re-sharing with
        # nothing new to add is a no-op, not an error.
        return {"ok": True, "unchanged": True,
                "note": "The hiring manager already sees this shortlist. "
                        "Comments and decisions reach them live.",
                "hm_share": {"shared_by": prior.get("shared_by"), "ts": prior.get("ts"),
                             "run_id": prior.get("run_id")}}
    run_id = str(meta.get("run_id", ""))
    snapshot = service.hm_share_snapshot(cfg, req)  # membership record (+ audit of state)
    n_new = len(updates["new_candidates"])
    verb = "expanded for" if prior is not None else "shared with"
    service.record_req_event(
        req_id, ident[0], "shortlist_shared",
        f"Shortlist {verb} the hiring manager — {len(snapshot)} candidate(s) visible",
        data={"run_id": run_id, "hm_email": hm_email, "snapshot": snapshot,
              "resend": prior is not None})
    title = service.display_title(req)
    actor_name = names.display_name(ident[0], req_meta=req_m)
    if prior is None:
        notify(hm_email,
               f"{title}: {actor_name} shared a shortlist with you — "
               f"{len(decided)} candidate{'s' if len(decided) != 1 else ''} to review.",
               actor=ident[0], req_id=req_id, data={"kind": "shortlist_shared"})
    else:
        # A re-share pushes decision changes (a snapshot refresh) and/or newly
        # added candidates — notify per category, each deep-linked to what moved.
        changed = updates["decisions"]
        if changed:
            notify(hm_email,
                   f"{title}: {actor_name} updated decisions on {len(changed)} "
                   f"candidate{'s' if len(changed) != 1 else ''} — the shared shortlist is refreshed.",
                   actor=ident[0], req_id=req_id,
                   data={"kind": "hm_decisions_updated", "candidate_ids": changed})
        if n_new:
            notify(hm_email,
                   f"{title}: {actor_name} added {n_new} more "
                   f"candidate{'s' if n_new != 1 else ''} to your shortlist.",
                   actor=ident[0], req_id=req_id,
                   data={"kind": "shortlist_shared", "candidate_ids": updates["new_candidates"]})
    fresh = service.hm_share_record(req_id, meta) or {}
    return {"ok": True, "hm_share": {"shared_by": fresh.get("shared_by"),
                                     "ts": fresh.get("ts"),
                                     "run_id": fresh.get("run_id")}}


def _compose_decision(meta: dict, rec: dict, action: str, free_text: str, user: str,
                      req_id: str):
    """One Decision from a review action — shared by the single and batch routes
    so stamping (suggestion/versions/reviewer/req_id) can never drift between them.

    Since P34 the action lives ONLY in (human_action, final_band); the reason is
    the recruiter's pure free text. Because the feedback log rejects empty
    reasons (a guarantee we keep), a note-less decision stores the bare action
    label — service.decision_note() treats exactly that as "no note"."""
    from resume_screener.feedback import versions_from_run_meta
    from resume_screener.models import Decision

    if action == "clear":
        # Un-bucketing IS a logged human action (they changed their mind to
        # undecided); rerank with no final band = cleared (service._is_cleared).
        human_action, final_band = "rerank", None
        reason = free_text or "Cleared — returned to undecided"
    else:
        human_action = service.ACTION_TO_HUMAN[action]  # forward=agree, discuss=rerank, not=override
        final_band = service.BAND_FOR_ACTION[action]
        reason = free_text or service.ACTION_LABEL[action]

    s = rec["score"]
    return Decision(
        candidate_id=s["candidate_id"],
        run_id=str(meta.get("run_id", "")),
        req_id=req_id,  # scopes carry-forward to THIS req (2026-07-21)
        suggestion=f"{s['fit_band']} ({round(s['final_score'])})",
        human_action=human_action,
        final_band=final_band,
        reason=reason,
        reviewer=user,
        versions=versions_from_run_meta(meta),
        ts=_now(),
    )


_DECISION_ACTIONS = ("forward", "discuss", "not", "clear")


def _block_decisions_when_stale(req, meta: dict, action: str) -> None:
    """P51: NEW decisions on scores produced under older published rules are
    refused (409) — the run must be re-screened first. `clear` stays allowed:
    removing reliance on a stale score must never be trapped. Decisions already
    recorded are facts and stay in the log untouched."""
    if action == "clear":
        return
    drift = service.config_drift(service.config(), req, meta)
    if not any(drift["changed"].values()):
        return
    areas = [k for k, v in drift["changed"].items() if v]
    raise HTTPException(409, detail={
        "code": "stale_run",
        "message": (service._stale_summary(areas)
                    + " Re-screen this role to decide on current scores."),
        "stale": {"changed": areas, "details": drift["details"]},
    })


@app.get("/api/roles/{req_id}/decisions")
def list_decisions_route(req_id: str,
                         ident: tuple[str, str] = Depends(_recruiter_or_read)) -> dict:
    """The dashboard's recorded-decisions popup: the latest decision per
    candidate on the current run, with display names — no need to open the
    requisition to see what was decided. Outside the requisition's team it says
    so rather than listing nothing (2026-10-21)."""
    req = _req_authorized(req_id, ident)
    if not _sees_decisions(req, ident[0], _request_caps()):
        return {"req_id": req_id, "title": req.role_title, "decisions": [],
                "hidden": True, "message": decision_view.HIDDEN_MESSAGE}
    private_map = service._private_map(req_id)
    rows = []
    for cid, decision in service.role_decision_records(req_id).items():
        rows.append({
            "candidate_id": cid,
            "name": service.candidate_name(req_id, cid, private_map),
            "action": service.action_from_decision(decision),
            "reason": service.decision_note(decision),
            "reviewer": decision.reviewer,
            "ts": decision.ts,
        })
    rows.sort(key=lambda r: r["ts"] or "")
    return {"req_id": req_id, "title": req.role_title, "decisions": rows}


@app.post("/api/roles/{req_id}/decisions")
def record_decision_route(
    req_id: str,
    payload: dict = Body(...),
    ident: tuple[str, str] = Depends(_recruiter),
) -> dict:
    from resume_screener.feedback import record_decision

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _team_or_403(req, ident)  # decisions are the team's (2026-10-21)
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(400, "This role hasn't been screened yet.")
    candidate_id = str(payload.get("candidate_id", "")).strip()
    action = str(payload.get("action", "")).strip()
    if action not in _DECISION_ACTIONS:
        raise HTTPException(400, "Action must be one of: forward, discuss, not, clear.")
    _block_decisions_when_stale(req, meta, action)

    rec = next((r for r in service.read_candidate_records(req_id)
                if r["score"]["candidate_id"] == candidate_id), None)
    if rec is None:
        raise HTTPException(404, "No such candidate in this run.")
    free_text = str(payload.get("reason", "")).strip()
    decision = _compose_decision(meta, rec, action, free_text, ident[0], req_id)
    record_decision(decision)
    # The updated entry + fresh counts ride back so the Review page reconciles
    # in place — no whole-payload refetch per click. reason is the display note
    # ("" when none was typed), exactly what the cards render; a "clear" comes
    # back as action "clear" so the client nulls its bucket.
    return {
        "ok": True,
        "action": action,
        "decision": {"candidate_id": candidate_id, "action": action,
                     "reason": free_text, "ts": decision.ts},
        "decision_counts": review.decision_counts_for(req),
    }


@app.post("/api/roles/{req_id}/decisions/batch")
def record_decisions_batch(
    req_id: str,
    payload: dict = Body(...),
    ident: tuple[str, str] = Depends(_recruiter),
) -> dict:
    """One review action applied to many candidates — the bulk bar's ONE call.
    All-or-nothing: everything is validated before the first line is appended."""
    from resume_screener.feedback import record_decision

    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _team_or_403(req, ident)  # decisions are the team's (2026-10-21)
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(400, "This role hasn't been screened yet.")
    action = str(payload.get("action", "")).strip()
    if action not in _DECISION_ACTIONS:
        raise HTTPException(400, "Action must be one of: forward, discuss, not, clear.")
    _block_decisions_when_stale(req, meta, action)
    candidate_ids = [str(c).strip() for c in payload.get("candidate_ids") or [] if str(c).strip()]
    if not candidate_ids:
        raise HTTPException(400, "Select at least one candidate.")

    records = {r["score"]["candidate_id"]: r for r in service.read_candidate_records(req_id)}
    unknown = [cid for cid in candidate_ids if cid not in records]
    if unknown:
        raise HTTPException(404, "Not in this run: " + ", ".join(sorted(unknown)))

    free_text = str(payload.get("reason", "")).strip()
    user = ident[0]  # the real reviewer: SSO email when hosted, DEV_USER locally
    for cid in candidate_ids:
        record_decision(_compose_decision(meta, records[cid], action, free_text, user, req_id))
    return {
        "ok": True,
        "applied": len(candidate_ids),
        "decisions": {cid: {"action": action, "reason": free_text} for cid in candidate_ids},
        "decision_counts": review.decision_counts_for(req),
    }


def _reconfirm(meta: dict, rec: dict, carried, user: str):
    """Re-record a carried-forward decision on the CURRENT run — same action +
    note, restamped to today's scores — so its reconfirm badge clears."""
    from resume_screener.feedback import record_decision, versions_from_run_meta
    from resume_screener.models import Decision

    s = rec["score"]
    record_decision(Decision(
        candidate_id=s["candidate_id"], run_id=str(meta.get("run_id", "")),
        req_id=carried.req_id or "",  # same req as the carried decision (req-scoped)
        suggestion=f"{s['fit_band']} ({round(s['final_score'])})",
        human_action=carried.human_action, final_band=carried.final_band,
        reason=carried.reason, reviewer=user,
        versions=versions_from_run_meta(meta), ts=_now()))


@app.post("/api/roles/{req_id}/candidates/{candidate_id}/reconfirm")
def reconfirm_decision_route(
    req_id: str, candidate_id: str,
    ident: tuple[str, str] = Depends(_recruiter),
) -> dict:
    """Re-affirm a decision carried forward from an earlier run onto the current
    scores (2026-07-20). Records a fresh decision on THIS run with the same
    action + note, clearing the 'decided under previous scores' badge. It asks no
    new judgment — a one-click 'this still stands on the new scores'."""
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _team_or_403(req, ident)  # decisions are the team's (2026-10-21)
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(400, "This role hasn't been screened yet.")
    _block_decisions_when_stale(req, meta, "reconfirm")  # re-screen a still-stale run first
    carried = service.role_decision_records(req_id).get(candidate_id)
    if carried is None:
        raise HTTPException(404, "No decision to reconfirm for this candidate.")
    rec = next((r for r in service.read_candidate_records(req_id)
                if r["score"]["candidate_id"] == candidate_id), None)
    if rec is None:
        raise HTTPException(404, "No such candidate in this run.")
    _reconfirm(meta, rec, carried, ident[0])
    return {"ok": True, "action": service.action_from_decision(carried),
            "decision_counts": review.decision_counts_for(req)}


@app.post("/api/roles/{req_id}/decisions/reconfirm-all")
def reconfirm_all_route(
    req_id: str, ident: tuple[str, str] = Depends(_recruiter),
) -> dict:
    """Reconfirm every carried decision the current scores actually moved — the
    'Keep all decisions' button after a re-screen. Decisions unaffected by the
    change are already current and are left alone."""
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    _team_or_403(req, ident)  # decisions are the team's (2026-10-21)
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(400, "This role hasn't been screened yet.")
    _block_decisions_when_stale(req, meta, "reconfirm")
    records = {r["score"]["candidate_id"]: r for r in service.read_candidate_records(req_id)}
    n = 0
    for cid, carried in service.role_decision_records(req_id).items():
        if not service.decision_reconfirm(carried, meta)["needs_reconfirm"]:
            continue
        rec = records.get(cid)
        if rec is None:
            continue
        _reconfirm(meta, rec, carried, ident[0])
        n += 1
    return {"ok": True, "reconfirmed": n, "decision_counts": review.decision_counts_for(req)}


@app.put("/api/roles/{req_id}/candidates/{candidate_id}/note")
def save_self_note_route(req_id: str, candidate_id: str, payload: dict = Body(...),
                         ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Save the signed-in recruiter's PRIVATE note on a candidate (2026-07-20).
    Saves on its own — no decision needed (the pilot's 'no way to save' fix) —
    is private to its author, and survives a re-screen (keyed by candidate, not
    run). Empty text clears it."""
    from resume_screener import self_notes

    _req_authorized(req_id, ident)
    rec = self_notes.save_note(req_id, candidate_id, author=ident[0],
                               text=str(payload.get("text", "")))
    return {"ok": True, "text": rec["text"], "ts": rec["ts"]}


@app.get("/api/roles/{req_id}/candidates/{candidate_id}/resume")
def download_resume(req_id: str, candidate_id: str, inline: bool = False,
                    vident: tuple[str, str, list[str]] = Depends(viewer_identity)):  # noqa: ANN201
    """The ORIGINAL résumé (with the candidate's PII). Reachable by the recruiting
    side and — for a candidate on their SHARED shortlist — the assigned hiring
    manager (2026-07-20): an HM reviews the real résumé, not a redacted stand-in.
    ?inline=1 lets the drawer embed the PDF (no local download needed to read it).

    SOURCE SEAM: today the bytes come from local storage (service.resume_path). A
    future iCIMS / OneDrive / SharePoint / Blob loader (SSO-backed) slots in here
    behind the same route + ?inline contract, so the drawer's PDF viewer never
    changes — only where the bytes come from."""
    req = _req_view_authorized(req_id, vident[0], vident[2])
    _hm_candidate_or_403(req, candidate_id, vident[2])  # HM: only a shared candidate
    path = service.resume_path(req_id, candidate_id)
    if path is None:
        raise HTTPException(404, "Original résumé file not found for this candidate.")
    return _resume_response(path, inline, req_id)


@app.post("/api/roles/{req_id}/handoff")
def ask_claude_handoff(req_id: str, payload: dict = Body(default={}),
                       vident: tuple[str, str, list[str]] = Depends(viewer_identity),
                       _rl: None = Depends(ratelimit.limited("handoff"))) -> dict:
    """Assemble the redacted 'Ask Claude' context bundle for this requisition and
    record the copy (P44). Reachable by the recruiting team; the audit event is
    emitted atomically with producing the text. Off unless the ask_claude_handoff
    flag is on (2026-10-23) — refused here, not only hidden in the UI."""
    from resume_screener import feature_flags
    from resume_screener.api import handoff

    if not feature_flags.flag_enabled(handoff.FLAG):
        raise HTTPException(403, handoff.DISABLED_MESSAGE)
    req = _req_view_authorized(req_id, vident[0], vident[2])
    if not verdict.allowed(vident[2]):
        # Every line of the bundle is the AI's read on a candidate — there is no
        # verdict-free version worth copying out.
        raise HTTPException(403, verdict.DENIED_MESSAGE)
    if _is_hm_viewer(vident[2]):
        # The bundle covers every candidate screened, and a hiring manager sees the
        # ones shared with them (2026-10-21).
        raise HTTPException(403, "The Ask-Claude bundle covers every candidate on the "
                                 "requisition, so it is for the recruiting team.")
    blind = bool(payload.get("blind", False))
    text = handoff.build_bundle(req, blind=blind,
                                decisions=_sees_decisions(req, vident[0], vident[2]))
    service.record_req_event(req_id, vident[0], "handoff_copied",
                             handoff.COPIED_SUMMARY.format(user=vident[0]))
    return {"text": text}


# ============================ HM PORTAL (P41) ============================
#
# The hiring-manager collaboration surface. The WHOLE surface is dark unless the
# hm_portal flag is on (_hm_portal_or_403 / _assigned_hm), on top of P37's
# capability suppression. Takes are INPUT — they NEVER write decisions.jsonl.


@app.put("/api/roles/{req_id}/hm")
def assign_hm(req_id: str, payload: dict = Body(...),
              ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Assign a hiring manager to this requisition (recruiter action)."""
    from resume_screener.ats.local_folder import save_req_meta
    from resume_screener.notify import notify

    _hm_portal_or_403()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    email = str(payload.get("email", "")).strip()
    name = str(payload.get("name", "")).strip()
    if not email:
        raise HTTPException(400, "The hiring manager's email is required.")
    hm = {"email": email, "name": name, "status": "invited"}
    save_req_meta(req.jd_ref, hm=hm)
    service.record_req_event(req_id, ident[0], "hm_assigned",
                             f"Assigned hiring manager {name or email}")
    notify(email, f"You've been invited to review {service.display_title(req)}.",
           actor=ident[0], req_id=req_id, data={"kind": "hm_invited"})
    return {"ok": True, "hm": hm}


@app.post("/api/roles/{req_id}/suggestions")
def create_suggestion(req_id: str, payload: dict = Body(...),
                      ctx: tuple = Depends(_assigned_hm)) -> dict:
    """The assigned HM proposes a checklist edit (INPUT — the TA publishes)."""
    from resume_screener import suggestions
    from resume_screener.notify import notify

    user, _role, req = ctx
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    new_text = str(payload.get("new_text", "")).strip()
    if not new_text:
        raise HTTPException(400, "Suggest the new wording for the requirement.")
    # An empty requirement_id ⇒ the HM is proposing a brand-new checklist row
    # (2026-07-20), not editing an existing one. Same review/approval flow.
    is_new_row = not str(payload.get("requirement_id", "")).strip()
    # 2026-07-19: a suggestion may change the KIND and the IMPORTANCE too —
    # validated here, applied only when the recruiter accepts.
    new_kind = str(payload.get("new_kind", "") or "").strip() or None
    if new_kind and new_kind not in ("nice_to_have", "must_have", "hard_gate"):
        raise HTTPException(400, "The kind must be nice-to-have, must-have or hard-gate.")
    new_importance = payload.get("new_importance")
    if new_importance is not None:
        try:
            new_importance = int(new_importance)
        except (TypeError, ValueError):
            raise HTTPException(400, "Importance must be a number from 1 to 5.")
        if not 1 <= new_importance <= 5:
            raise HTTPException(400, "Importance must be a number from 1 to 5.")
    hm = service.req_meta(req).get("hm") or {}
    record = suggestions.add_suggestion(
        req.jd_ref, req_id=req_id,
        requirement_id=str(payload.get("requirement_id", "")).strip(),
        old_text=str(payload.get("old_text", "")).strip(), new_text=new_text,
        note=str(payload.get("note", "")).strip(), actor=user, actor_name=hm.get("name", ""),
        old_kind=str(payload.get("old_kind", "") or "").strip() or None,
        new_kind=new_kind,
        old_importance=payload.get("old_importance"),
        new_importance=new_importance,
    )
    from resume_screener import names
    actor_name = names.display_name(user, req_meta=service.req_meta(req))
    what = "a new checklist item" if is_new_row else "a checklist edit"
    service.record_req_event(req_id, user, "suggestion_created",
                             f"{actor_name} suggested {what}")
    created_by = service.req_meta(req).get("created_by")
    if created_by:
        notify(created_by, f"{actor_name} suggested {what}.",
               actor=user, req_id=req_id,
               data={"kind": "suggestion_created",
                     "suggestion_id": record.get("suggestion_id")})
    return record


@app.get("/api/roles/{req_id}/suggestions")
def list_suggestions_route(req_id: str,
                           vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """Checklist suggestions for a requisition (P42): the TA renders the OPEN
    ones as inline SuggestionCards on the Checklist tab; the assigned HM sees
    open ones as 'waiting' AND the decided ones with the outcome + note
    (2026-07-19 — before this, an HM never learned what happened). Clients
    filter by status. Flag-gated + view-scoped."""
    from resume_screener import suggestions

    _hm_portal_or_403()
    req = _req_view_authorized(req_id, vident[0], vident[2])
    return {"suggestions": suggestions.list_suggestions(req.jd_ref)}


def _decide_suggestion(suggestion_id: str, ident: tuple[str, str], status: str,
                       payload: dict) -> dict:
    from resume_screener import suggestions
    from resume_screener.notify import notify

    _hm_portal_or_403()
    cfg = service.config()
    found = suggestions.locate(cfg.ats.jd_dir, suggestion_id)
    if found is None:
        raise HTTPException(404, "No such suggestion.")
    jd_md, record = found
    req_id = record["req_id"]
    req = _req_authorized(req_id, ident)  # region + existence for the recruiter
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    note = str(payload.get("note", "")).strip()
    edited = payload.get("edited_text")
    edited = str(edited).strip() if edited else None
    updated = suggestions.decide(jd_md, suggestion_id, status=status,
                                 decided_by=ident[0], decided_note=note, edited_text=edited)
    if updated is None:
        raise HTTPException(400, "That suggestion has already been decided.")
    verb = "accepted" if status == "accepted" else "declined"
    service.record_req_event(req_id, ident[0], f"suggestion_{verb}",
                             f"{verb.capitalize()} a hiring-manager suggestion")
    notify(updated["actor"], f"Your checklist suggestion was {verb}"
           + (f": {note}" if note else "."), actor=ident[0], req_id=req_id,
           data={"kind": f"suggestion_{verb}", "suggestion_id": suggestion_id})
    return {"ok": True, "status": status, "req_id": req_id,
            "requirement_id": updated["requirement_id"], "merged_text": updated["new_text"],
            "merged_kind": updated.get("new_kind"),
            "merged_importance": updated.get("new_importance"),
            # No requirement_id ⇒ the HM proposed a NEW row: the editor APPENDS it
            # (rather than splicing into an existing row) then publishes as usual —
            # a flagged new row still routes through HR-Head approval.
            "new_row": not str(updated.get("requirement_id") or "").strip()}


@app.post("/api/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: str, payload: dict = Body(default={}),
                      ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    """Accept a suggestion — returns merged_text for the checklist editor draft
    (the editor merges + publishes as usual; nothing here writes the rubric)."""
    return _decide_suggestion(suggestion_id, ident, "accepted", payload or {})


@app.post("/api/suggestions/{suggestion_id}/decline")
def decline_suggestion(suggestion_id: str, payload: dict = Body(default={}),
                       ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    return _decide_suggestion(suggestion_id, ident, "declined", payload or {})


@app.post("/api/suggestions/{suggestion_id}/withdraw")
def withdraw_suggestion(suggestion_id: str,
                        vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """The HM pulls back their OWN open suggestion before the recruiter decides
    (2026-07-19). Author-checked in the store; anyone else gets a 403."""
    from resume_screener import suggestions

    _hm_portal_or_403()
    cfg = service.config()
    found = suggestions.locate(cfg.ats.jd_dir, suggestion_id)
    if found is None:
        raise HTTPException(404, "No such suggestion.")
    jd_md, record = found
    req_id = record["req_id"]
    _req_view_authorized(req_id, vident[0], vident[2])
    updated = suggestions.withdraw(jd_md, suggestion_id, actor=vident[0])
    if updated is None:
        if str(record.get("actor", "")).lower() != vident[0].lower():
            raise HTTPException(403, "Only the person who suggested this can withdraw it.")
        raise HTTPException(400, "That suggestion has already been decided.")
    service.record_req_event(req_id, vident[0], "suggestion_withdrawn",
                             "Hiring manager withdrew a checklist suggestion")
    return {"ok": True, "status": "withdrawn", "suggestion_id": suggestion_id}


@app.post("/api/roles/{req_id}/takes")
def record_hm_takes(req_id: str, payload: dict = Body(...),
                    ctx: tuple = Depends(_assigned_hm)) -> dict:
    """Record the assigned HM's takes (single or bulk). INPUT — never a decision;
    decisions.jsonl is untouched."""
    from resume_screener import hm_takes

    user, _role, req = ctx
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(400, "This role hasn't been screened yet.")
    raw = payload.get("takes") or []
    if not raw:
        raise HTTPException(400, "Provide at least one take.")
    known = {r["score"]["candidate_id"] for r in service.read_candidate_records(req_id)}
    unknown = [str(t.get("candidate_id", "")) for t in raw
               if str(t.get("candidate_id", "")) not in known]
    if unknown:
        raise HTTPException(404, "Not in this run: " + ", ".join(sorted(unknown)))
    hm = service.req_meta(req).get("hm") or {}
    try:
        written = hm_takes.record_takes(req_id, str(meta.get("run_id", "")), raw,
                                        hm_email=user, hm_name=hm.get("name", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "recorded": len(written)}


@app.post("/api/roles/{req_id}/takes/send")
def send_hm_takes(req_id: str, ctx: tuple = Depends(_assigned_hm)) -> dict:
    from resume_screener.notify import notify

    from resume_screener import names

    user, _role, req = ctx
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    actor_name = names.display_name(user, req_meta=service.req_meta(req))
    service.record_req_event(req_id, user, "hm_takes_sent", f"{actor_name} sent their takes")
    created_by = service.req_meta(req).get("created_by")
    if created_by:
        notify(created_by, f"{actor_name} sent their candidate takes.",
               actor=user, req_id=req_id, data={"kind": "hm_takes_sent"})
    return {"ok": True}


@app.get("/api/roles/{req_id}/activity")
def req_activity(req_id: str,
                 vident: tuple[str, str, list[str]] = Depends(viewer_identity)) -> dict:
    """The Checklist-tab activity rail — dual-timezone entries (P41/P42)."""
    _hm_portal_or_403()
    req = _req_view_authorized(req_id, vident[0], vident[2])
    return {"entries": service.req_activity(req)}


@app.post("/api/roles/{req_id}/request-hm-review")
def request_hm_review(req_id: str, ident: tuple[str, str] = Depends(_recruiter)) -> dict:
    from resume_screener.notify import notify

    from resume_screener.ats.local_folder import save_req_meta

    _hm_portal_or_403()
    req = _req_authorized(req_id, ident)
    _open_req_or_409(req)  # closed = no new hiring work (RS-06)
    hm = service.req_meta(req).get("hm") or {}
    if not hm.get("email"):
        raise HTTPException(400, "Assign a hiring manager first.")
    # Durable signal for the HM's requisitions list ("requested your review") —
    # no event scan needed on the card path.
    save_req_meta(req.jd_ref, hm={**hm, "status": "review_requested"})
    service.record_req_event(req_id, ident[0], "hm_review_requested",
                             "Requested hiring-manager review")
    notify(hm["email"], f"Your review is requested for {service.display_title(req)}.",
           actor=ident[0], req_id=req_id, data={"kind": "hm_review_requested"})
    return {"ok": True}


# ============================ PREFERENCES (P39) ============================
#
# User-scoped hiring preferences: a plain-language nudge the owner wants applied
# to every requisition they manage (bounded by P40). Every preference waits for an
# HR Head's sign-off (2026-10-16, pen-test RS-01 — clean ones used to go live on
# their own); protected-attribute ones are blocked outright; nobody approves their
# own. Nothing here changes a score — P40 owns that.

_PREF_STATUS_LABEL = {
    "pending": "Waiting for HR Head sign-off",
    "approved": "Approved",
    "rereview": "Re-review needed — suspended until re-approved",
    "declined": "Declined",
    "blocked": "Blocked",
    "withdrawn": "Withdrawn",
}


def _pref_ui(rec: dict) -> dict:
    """Recruiter-plain row for a folded preference record."""
    from resume_screener.scoring_model import load_scoring_model

    status = rec.get("display_status", rec.get("status", ""))
    classification = rec.get("classification") or {}
    label = _PREF_STATUS_LABEL.get(status, status)
    if status == "approved" and rec.get("approved_by"):
        label = f"Approved · {rec['approved_by']}"
    # P51: the cap sentence reads the GOVERNED value — never a literal that rots.
    cap = load_scoring_model().preferences.per_preference_cap_points
    return {
        "id": rec.get("pref_id"),
        "text": rec.get("text", ""),
        "owner": rec.get("owner"),
        "owner_name": rec.get("owner_name"),
        "status": status,
        "status_label": label,
        "level": classification.get("level"),
        # the classifier's plain sentence — shown verbatim on warn/blocked rows
        "warning": classification.get("reason") if classification.get("level") != "ok" else None,
        "cap_note": f"Capped at +{cap:g} if approved.",
        "approved_by": rec.get("approved_by"),
        "approved_at": rec.get("approved_at"),
        "review_due": rec.get("review_due"),
        "decline_note": rec.get("decline_note"),
        "ts": rec.get("ts"),
    }


@app.get("/api/preferences")
def list_preferences(ident: tuple[str, str] = Depends(_prefs_own)) -> dict:
    from resume_screener import preferences

    rows = [_pref_ui(p) for p in preferences.list_prefs(owner=ident[0])]
    return {"preferences": rows}


@app.post("/api/preferences/check")
def check_preference(payload: dict = Body(...),
                     ident: tuple[str, str] = Depends(_prefs_own),
                     _rl: None = Depends(ratelimit.limited("pref_check"))) -> dict:
    """Dry-run classification for the composer — no write. The client shows the
    warn/blocked message before committing. The phrase lists plus the intent
    screen (preferences.screen_pref) — the same verdict adding would record."""
    from resume_screener import preferences

    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Type a preference first.")
    return preferences.screen_pref(text).model_dump()


@app.post("/api/preferences")
def add_preference_route(payload: dict = Body(...),
                         ident: tuple[str, str] = Depends(_prefs_own),
                         _rl: None = Depends(ratelimit.limited("pref_check"))) -> dict:
    from resume_screener import preferences

    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Type a preference first.")
    rec = preferences.add_preference(ident[0], text, actor_role=ident[1],
                                     check=preferences.screen_pref(text))
    return _pref_ui(rec)


@app.delete("/api/preferences/{pref_id}")
def withdraw_preference(pref_id: str, ident: tuple[str, str] = Depends(_prefs_own)) -> dict:
    from resume_screener import preferences

    rec = preferences.withdraw(pref_id, owner=ident[0], actor_role=ident[1])
    if rec is None:
        raise HTTPException(404, "No such preference of yours.")
    return _pref_ui(rec)


@app.get("/api/preferences/pending")
def pending_preferences(ident: tuple[str, str] = Depends(_prefs_approve)) -> dict:
    """The HR-Head approval queue: every user's pending + due-for-re-review prefs."""
    from resume_screener import preferences

    rows = [_pref_ui(p) for p in preferences.list_prefs()
            if p["display_status"] in ("pending", "rereview")]
    return {"pending": rows, "count": len(rows)}


@app.post("/api/preferences/approve")
def approve_preferences(payload: dict = Body(...),
                        ident: tuple[str, str] = Depends(_prefs_approve)) -> dict:
    from resume_screener import preferences

    pref_ids = [str(p).strip() for p in payload.get("pref_ids") or [] if str(p).strip()]
    if not pref_ids:
        raise HTTPException(400, "Select at least one preference to approve.")
    own = preferences.own_pref_ids(pref_ids, ident[0])
    if own and len(own) == len(pref_ids):
        raise HTTPException(403, "You can't approve your own preference — another HR Head "
                                 "(or the Super Admin) needs to sign it off.")
    approved = preferences.approve(pref_ids, approver=ident[0], approver_role=ident[1])
    # 2026-07-19: the owner learns the outcome (bell notification).
    from resume_screener.notify import notify
    for rec in approved:
        owner = str(rec.get("owner", "")).strip()
        if owner and owner.lower() != ident[0].lower():
            notify(owner, f"Your screening preference “{rec.get('text', '')}” was approved "
                          f"by {ident[0]} — it applies from the next screen.",
                   actor=ident[0], data={"kind": "pref_approved"})
    return {"ok": True, "approved": len(approved), "skipped_own": len(own),
            "preferences": [_pref_ui(r) for r in approved]}


@app.post("/api/preferences/{pref_id}/decline")
def decline_preference(pref_id: str, payload: dict = Body(default={}),
                       ident: tuple[str, str] = Depends(_prefs_approve)) -> dict:
    from resume_screener import preferences

    note = str((payload or {}).get("note", "")).strip()
    rec = preferences.decline(pref_id, note=note, approver=ident[0], approver_role=ident[1])
    if rec is None:
        raise HTTPException(404, "No pending preference with that id.")
    owner = str(rec.get("owner", "")).strip()
    if owner and owner.lower() != ident[0].lower():
        from resume_screener.notify import notify
        notify(owner, f"Your screening preference “{rec.get('text', '')}” was declined"
                      + (f": {note}" if note else "."),
               actor=ident[0], data={"kind": "pref_declined"})
    return _pref_ui(rec)


# ============================ GOVERNANCE ============================


# --- the scoring model: read, per-knob metadata, live preview -------------------------
#
# Since the 2026-07-18 merge the model carries the AI signals + never-scored
# topics too — the old GET /api/signals is gone; everything reads from here.


@app.get("/api/scoring-config", dependencies=[Depends(require_any(
    "recruiter", "hm_take", "changes_approve", "prefs_approve", "rules_view"))])
def get_scoring_config() -> dict:
    """The published scoring values + resolved plain-language display strings —
    the single source every on-screen scoring fact reads from (never a literal
    in JSX, so the copy can't rot when the model changes)."""
    from resume_screener.governance import load_audit
    from resume_screener.scoring_model import load_scoring_model, resolved_display

    sm = load_scoring_model()
    published = next((e for e in reversed(load_audit())
                      if e.get("action") == "publish_scoring_model"
                      and e.get("version") == sm.version), None)
    return {
        "version": sm.version,
        "published_by": (published or {}).get("author"),
        "approved_by": (published or {}).get("approved_by"),
        "published_at": (published or {}).get("ts"),
        "bands": {"strong_at": sm.bands.strong_at, "possible_at": sm.bands.possible_at,
                  "cap_if_must_have_missing": sm.bands.cap_if_must_have_missing},
        "clamp": {"min": sm.clamp.min, "max": sm.clamp.max},
        "coverage": {"match_value": sm.coverage.match_value},
        "adjustments": {"total_cap_points": sm.adjustments.total_cap_points},
        "preferences": {"per_preference_cap_points": sm.preferences.per_preference_cap_points,
                        "review_months": sm.preferences.review_months},
        "confidence": sm.confidence.model_dump(),
        "display": resolved_display(sm),
    }


@app.get("/api/scoring/conflicts", dependencies=[Depends(_rules_view)])
def get_scoring_conflicts_route() -> dict:
    """Config-to-config staleness (Phase 3): scoring SIGNALS that conflict with a
    currently in-force jurisdiction BLOCKED pattern — the org scores something a
    jurisdiction forbids. Derived on read; the Scoring page shows these as
    'stale — conflicts with {jurisdiction}' so Legal/HR Head reconcile the model.
    Per-req enforcement is separate (the critic suspends blocked-factor evidence),
    so new reqs stay compliant regardless."""
    from resume_screener import cascade

    cfg = service.config()
    try:
        conflicts = cascade.scoring_conflicts(cfg.policy.policies_dir)
    except Exception:  # noqa: BLE001 — a policy/model read must never break the page
        conflicts = []
    return {"conflicts": conflicts}


@app.get("/api/scoring-model/effective", dependencies=[Depends(_rules_view)])
def get_effective_scoring_model(jurisdiction: str | None = None) -> dict:
    """The scoring model AS IT APPLIES in one jurisdiction — or globally.

    There is one model for the whole organisation; a jurisdiction's policy pack
    turns the dial (which signals fire there, their caps, their market settings)
    and adds never-scored topics on top of the universal floor. Nothing showed
    that: this page presented the one model as "India (IN)" and its region
    selector snapped back, so a policy maker anywhere else had no way to see what
    the model does for their requisitions (2026-09-12).

    Every row here is derived from the SAME call a screening run makes
    (registry.apply_policy over the resolved policy), so this view cannot drift
    from what a run would do. Unknown jurisdiction -> 400, never a fallback.

    Guarded by rules_view, not by the recruiter dependency: Legal holds rules_view
    and is exactly who reads this."""
    from resume_screener import effective_model
    from resume_screener.policy import PolicyError
    from resume_screener.registry import RegistryError

    code = (jurisdiction or "").strip() or None
    try:
        return effective_model.effective_view(code, service.config().policy.policies_dir)
    except (PolicyError, RegistryError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/scoring-model", dependencies=[Depends(_rules_view)])
def get_scoring_model_route() -> dict:
    """The governance read: the full current model (knobs + signals + topics) +
    per-knob metadata (label, help, technical term, usual bounds, group) + the
    version history rail."""
    from resume_screener.governance import load_audit
    from resume_screener.scoring_model import (
        KNOB_META, canonical_dump, get_path, load_scoring_model,
        scoring_model_versions,
    )

    sm = load_scoring_model()
    knobs = [{"path": path, "value": get_path(sm, path), **meta}
             for path, meta in KNOB_META.items()]
    display = [{"path": f"display.{key}", "value": value} for key, value in sm.display.items()]
    by_version = {e.get("version"): e for e in load_audit()
                  if e.get("action") == "publish_scoring_model"}
    versions = []
    for n, _path in reversed(scoring_model_versions()):
        entry = by_version.get(n, {})
        versions.append({"version": n, "ts": entry.get("ts"),
                         "author": entry.get("author"),
                         "approved_by": entry.get("approved_by"),
                         "rationale": entry.get("rationale"),
                         "summary": entry.get("summary")})
    return {"version": sm.version, "knobs": knobs, "display": display,
            "signals": [s.model_dump() for s in sm.signals],
            "topics": [t.model_dump() for t in sm.topics],
            # the full document, section-ordered — what the page stages drafts from
            "model": canonical_dump(sm),
            "versions": versions,
            # the "median real move last quarter" fragment on the signals card
            "median_recent_move": service.median_recent_signal_move()}


@app.get("/api/scoring-model/versions/{n}", dependencies=[Depends(_rules_view)])
def get_scoring_model_version_route(n: int) -> dict:
    """One published version's full document — the Versions tab's "Restore this
    version" stages it as a draft (nothing changes until re-approved)."""
    from resume_screener.scoring_model import canonical_dump, load_scoring_model_version

    sm = load_scoring_model_version(n)
    if sm is None:
        raise HTTPException(404, f"No scoring model v{n} on record.")
    return {"version": n, "model": canonical_dump(sm)}


@app.post("/api/scoring/preview", dependencies=[Depends(_rules_view)])
def scoring_preview(payload: dict = Body(...)) -> dict:
    """The Scoring page's example candidate: a DRAFT model + a synthetic
    candidate → the real pure scoring functions. No model calls, no writes.
    The draft arrives as a FULL document (payload.model); dotted overrides
    (payload.values) on the current model are also accepted."""
    from resume_screener.api import score_math
    from resume_screener.governance import _set_dotted
    from resume_screener.scoring_model import KNOB_META, ScoringModel, load_scoring_model

    if payload.get("model"):
        data = dict(payload["model"])
        data.setdefault("version", load_scoring_model().version)
        data.setdefault("schema", 1)
    else:
        values = payload.get("values") or {}
        data = load_scoring_model().model_dump(by_alias=True)
        for path, value in values.items():
            if path not in KNOB_META and not str(path).startswith("display."):
                raise HTTPException(422, f"'{path}' is not a scoring setting.")
            _set_dotted(data, str(path), value)
    try:
        model = ScoringModel.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"These draft values don't form a valid model: {exc}")
    try:
        return score_math.run_preview(model, payload.get("candidate") or {})
    except score_math.PreviewError as exc:
        raise HTTPException(422, str(exc))


# --- change proposals (P51): propose → a SECOND leader approves → publish ------------
#
# The old instant PUT /api/signals and PUT /api/jurisdictions are gone: every
# scoring-affecting edit (rules, packs, the scoring model) lands here as a
# pending change and takes effect only when a different leader approves it.


def _changes_approve(request: Request) -> tuple[str, str]:
    return require("changes_approve",
                   "Approving changes is limited to the HR Head, Legal and the Super Admin.")(request)


def _change_http_error(exc: Exception) -> HTTPException:
    from resume_screener import change_requests as cr

    if isinstance(exc, cr.SelfApprovalError):
        return HTTPException(403, str(exc))
    if isinstance(exc, cr.ChangeConflictError):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


@app.post("/api/changes")
def propose_change_route(request: Request, payload: dict = Body(...)) -> dict:
    from resume_screener import change_requests as cr
    from resume_screener import rbac
    from resume_screener.policy import PolicyError
    from resume_screener.registry import RegistryError
    from resume_screener.scoring_model import ScoringModelError

    user, role = require_any("rules_edit", "scoring_edit",
                             message="Proposing changes isn't part of your role.")(request)
    kind = str(payload.get("kind", "")).strip()
    target = str(payload.get("target", "")).strip()
    body = payload.get("payload") or {}
    if kind not in cr.KINDS:
        raise HTTPException(400, f"Unknown change kind '{kind}'.")

    _user, _role, roles = rbac.get_identity_full(request.headers)
    caps = rbac.effective_capabilities(roles)
    if cr.required_capability(kind) not in caps:
        raise HTTPException(403, "Proposing this change isn't part of your role.")

    try:
        record = cr.propose_change(kind, target or kind, body,
                                   rationale=str(payload.get("rationale", "")),
                                   author=user, author_role=role)
    except (cr.ChangeRequestError, RegistryError, PolicyError, ScoringModelError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "change": record}


@app.get("/api/changes")
def list_changes_route(
    status: str | None = None, kind: str | None = None,
    ident: tuple[str, str] = Depends(require_any(
        "changes_approve", "rules_edit", "scoring_edit",
        message="The change queue isn't part of your role.")),
) -> dict:
    from resume_screener import change_requests as cr

    changes = cr.list_changes(status=status or None, kind=kind or None)
    return {"changes": changes,
            "pending_count": sum(1 for c in changes if c["status"] == "pending")
            if status in (None, "", "pending") else cr.pending_count()}


@app.post("/api/changes/{change_id}/approve")
def approve_change_route(change_id: str,
                         ident: tuple[str, str] = Depends(_changes_approve)) -> dict:
    from resume_screener import change_requests as cr
    from resume_screener.policy import PolicyError
    from resume_screener.registry import RegistryError
    from resume_screener.scoring_model import ScoringModelError

    try:
        record = cr.approve_change(change_id, approver=ident[0], approver_role=ident[1])
    except (cr.ChangeRequestError, cr.SelfApprovalError, cr.ChangeConflictError) as exc:
        raise _change_http_error(exc)
    except (RegistryError, PolicyError, ScoringModelError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "change": record, "version": record["resulting_version"]}


@app.post("/api/changes/{change_id}/decline")
def decline_change_route(change_id: str, payload: dict = Body(default={}),
                         ident: tuple[str, str] = Depends(_changes_approve)) -> dict:
    from resume_screener import change_requests as cr

    try:
        record = cr.decline_change(change_id, note=str((payload or {}).get("note", "")),
                                   approver=ident[0], approver_role=ident[1])
    except (cr.ChangeRequestError, cr.ChangeConflictError) as exc:
        raise _change_http_error(exc)
    return {"ok": True, "change": record}


@app.post("/api/changes/{change_id}/withdraw")
def withdraw_change_route(
    change_id: str,
    ident: tuple[str, str] = Depends(require_any(
        "rules_edit", "scoring_edit",
        message="Withdrawing a proposal isn't part of your role.")),
) -> dict:
    """The proposer pulls their own pending proposal — nothing changed."""
    from resume_screener import change_requests as cr

    try:
        record = cr.withdraw_change(change_id, author=ident[0])
    except (cr.ChangeRequestError, cr.SelfApprovalError, cr.ChangeConflictError) as exc:
        raise _change_http_error(exc)
    return {"ok": True, "change": record}


@app.post("/api/review-signoff")
def review_signoff(
    payload: dict = Body(...),
    ident: tuple[str, str] = Depends(
        require("rules_edit", "Recording the HR + Legal review is limited to the HR Head.")),
) -> dict:
    from resume_screener.governance import record_review

    user = ident[0]
    try:
        record_review(str(payload.get("reviewers", "")), str(payload.get("date", "")), author=user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/audit-log", dependencies=[Depends(_runs_audit)])
def get_audit_log() -> dict:
    from resume_screener.governance import load_audit

    entries = list(reversed(load_audit()))  # newest first
    return {"entries": entries}


# ============================ DATA MANAGEMENT (P47) ============================
#
# The storage map + the two deletion actions. data_manage-gated (HR Head + IT).
# Deletes require the operator to type the exact confirm phrase; both actions are
# doubly recorded — a data_deleted event (activity/audit feed) AND a governance
# audit entry (data_delete) — so the action is never silent.


@app.get("/api/data/summary")
def data_summary(ident: tuple[str, str] = Depends(_data_manage)) -> dict:
    from resume_screener import datamgmt

    return {"rows": datamgmt.storage_map(service.config()),
            "data_root": datamgmt.storage_root()}


def _record_delete(user: str, role: str, action: str, summary: str) -> None:
    from resume_screener import events, governance

    events.emit_event("data_deleted", actor=user, actor_role=role, summary=summary,
                      data={"action": action})
    governance.append_audit({"author": user, "action": "data_delete", "summary": summary})


@app.post("/api/data/delete-cache")
def data_delete_cache(payload: dict = Body(default={}),
                      ident: tuple[str, str] = Depends(_data_manage)) -> dict:
    from resume_screener import datamgmt

    if str(payload.get("confirm", "")).strip().lower() != "delete cache":
        raise HTTPException(400, 'Type "delete cache" to confirm.')
    datamgmt.delete_cache(service.config().runtime.cache_dir)
    _record_delete(ident[0], ident[1], "delete_cache", "Cache deleted.")
    return {"ok": True,
            "message": "Cache deleted. The next screening rebuilds it — nothing else changed."}


@app.post("/api/data/delete-candidates")
def data_delete_candidates(payload: dict = Body(default={}),
                           ident: tuple[str, str] = Depends(_data_manage)) -> dict:
    from resume_screener import datamgmt

    if str(payload.get("confirm", "")).strip().lower() != "delete all candidate data":
        raise HTTPException(400, 'Type "delete all candidate data" to confirm.')
    cfg = service.config()
    datamgmt.delete_candidate_data(cfg.ats.resumes_dir, cfg.ats.roles_dir)
    _record_delete(ident[0], ident[1], "delete_candidates",
                   "All candidate data deleted (the anonymous decision log was kept).")
    return {"ok": True,
            "message": "All résumés and screening results were deleted. "
                       "The anonymous decision log was kept — it is the audit trail."}


# ============================ RUNS & AUDIT ============================


@app.get("/api/runs", dependencies=[Depends(_runs_audit)])
def list_runs() -> dict:
    # The run index (DB when run_index_backend=db, else the output/ file scan) —
    # composition lives in service.list_runs so both backends share one shape. The
    # per-run "K strong" tally is a verdict: HR roles only.
    return {"runs": service.list_runs(show_verdict=verdict.allowed(_request_caps()))}


@app.get("/api/roles/{req_id}/audit", response_class=PlainTextResponse)
def audit_pack(req_id: str, ident: tuple[str, str] = Depends(_runs_audit)) -> str:
    _req_authorized(req_id, ident)  # region access lens (P38) applies to admins too
    meta = service.read_run_meta(req_id)
    if not meta:
        raise HTTPException(404, "No run for this role yet.")
    lines = [
        f"# Audit pack — {meta.get('role_title')}",
        f"Run: {meta.get('run_id')}",
        f"Jurisdiction: {meta.get('jurisdiction')} (pack {meta.get('policy_pack')})",
        f"Rubric: {meta.get('versions', {}).get('rubric')}",
        f"Registry: {meta.get('versions', {}).get('registry')}",
        f"Redaction enabled: {meta.get('redaction_enabled')}",
        "",
        "## Counts",
        *[f"- {k}: {v}" for k, v in meta.get("counts", {}).items()],
        "",
        *(["## Bands", *[f"- {k}: {v}" for k, v in meta.get("bands", {}).items()], ""]
          if verdict.allowed(_request_caps()) else []),
        "## Obligations checklist (displayed, not enforced)",
        *[f"- [ ] {o}" for o in meta.get("obligations", [])],
        "",
        "Decision-support only — nothing here rejects anyone; a human decides.",
    ]
    return "\n".join(lines)


# ============================ STATIC (built SPA) ============================


class _ImmutableStatic(StaticFiles):
    """Vite emits content-hashed asset filenames, so /assets/* can be cached
    forever — a content change ships a new filename. Saves a revalidation
    round-trip on every page load."""

    def file_response(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        resp = super().file_response(*args, **kwargs)
        resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return resp


# Local Entra SSO (dev only, LOCAL_SSO=1) — a no-op when the flag is unset.
# Registered HERE, deliberately: it must come BEFORE the SPA catch-all below, or
# `/{full_path:path}` swallows /.auth/* and sign-in 404s. It synthesizes the same
# X-MS-CLIENT-PRINCIPAL header Easy Auth injects hosted, so rbac.py is untouched.
from . import entra_sso  # noqa: E402 - import here to keep the ordering contract obvious

entra_sso.install(app)


_WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"
if _WEB_DIST.exists():
    app.mount("/assets", _ImmutableStatic(directory=_WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(request: Request, full_path: str):  # noqa: ANN201 — SPA fallback
        from resume_screener import rbac

        from . import signin_page

        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = _WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # An SSO deployment with no principal on the request: serve OUR front door
        # instead of booting the SPA, which would have nothing to show and would
        # render the no-access screen at someone whose session merely expired.
        #
        # Reachable only when Easy Auth is set to AllowAnonymous; under the default
        # RedirectToLoginPage the platform redirects before the request ever gets
        # here, and this branch is simply never taken. Correct either way.
        if modes.auth_required() and not rbac.has_principal(request.headers):
            signed_out = request.query_params.get("signed_out") == "1"
            return HTMLResponse(
                signin_page.render(next_url=request.query_params.get("next") or "/",
                                   signed_out=signed_out),
                headers={"Cache-Control": "no-store"},
            )
        # index.html must never be cached — it references the hashed assets and
        # must always fetch the current one after a deploy.
        return FileResponse(_WEB_DIST / "index.html", headers={"Cache-Control": "no-cache"})
