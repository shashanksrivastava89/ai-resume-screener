"""Region administration (the global rollout, 2026-09-12).

WHY THIS EXISTS. Region scoping has been declared since P38 and unusable ever
since: `grant_role` had no caller outside `screener db seed`, so every real SSO
user held no grants at all. `service._region_context_query` therefore treats an
UNSCOPED person as UNRESTRICTED — not as locked out — and its comment says why:

    "There is no API, CLI or admin page that grants a region — only db seed, for
    the dev user — so EVERY real SSO user resolves here. A gate nobody can
    administer is not access control, it is a trap. Enforcement therefore begins
    when someone is actually scoped. Tighten this the day granting regions is
    administrable, not before."

This is that day. The default does NOT change: unscoped still means unrestricted,
because a recruiter joining a global team should not need a second manual step
before they can see their own work. What changes is that scoping is now a
deliberate act someone can take, undo, and see the record of.

REGION IS ACCESS, NOT CAPABILITY. A grant narrows WHICH requisitions a person's
capabilities reach. It never grants one: those come from their Entra app roles.
Granting someone a region who is not a recruiter gives them nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from resume_screener.api import service

router = APIRouter(prefix="/api/access")


def _guard(request: Request, capability: str, message: str) -> tuple[str, str]:
    """Capability check, resolved lazily against app.py to avoid an import cycle."""
    from resume_screener import rbac

    user, role, roles = rbac.get_identity_full(request.headers)
    if capability not in rbac.effective_capabilities(roles):
        raise HTTPException(403, message)
    return user, role


def _require_ready() -> dict:
    """Refuse a WRITE when regions cannot be administered, saying which problem it
    is. A read degrades instead (see get_grants) — an admin opening the page
    deserves the explanation, not an error page."""
    state = service.region_admin_state()
    if state["status"] == "unavailable":
        raise HTTPException(503, state["message"])
    if state["status"] == "unseeded":
        raise HTTPException(409, state["message"])
    return state


@router.get("/grants")
def get_grants(request: Request) -> dict:
    """Who is scoped to what. Never 500s: an unseeded or unreachable database is a
    state with a message, because an administrator cannot fix what they cannot
    see."""
    _guard(request, "access_view", "Access & Roles is limited to the Super Admin.")
    state = service.region_admin_state()
    return {
        "status": state["status"],
        "message": state["message"],
        "regions": state["regions"],
        "people": service.access_people() if state["status"] == "ok" else [],
    }


@router.post("/grants")
def add_grant(request: Request, payload: dict = Body(...)) -> dict:
    """Give someone access to a region. `region: null` = every region.

    An all-regions grant REPLACES any scoped ones: `regions_for_user` reads a NULL
    row as "all", so a person holding both would be all-regions with dead rows
    beside it — a state nobody chose and nobody could read off the page."""
    from resume_screener import events, notify, people, regionmap
    from resume_screener.db.repositories import identity as identity_repo
    from resume_screener.db.session import session_scope

    user, role = _guard(request, "access_edit",
                        "Granting regions is limited to the Super Admin.")
    _require_ready()

    email = str(payload.get("email") or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "That address doesn't look right.")
    raw = payload.get("region")
    region = str(raw).strip().upper() if raw else None

    current = next((p for p in service.access_people() if p["email"] == email), None)
    if region and current and current["all_regions"]:
        name = people.display_name(email) or email
        raise HTTPException(409, f"{name} already sees every region. Remove "
                                 "'All regions' first to scope them.")

    try:
        with session_scope() as session:
            if region is None:
                identity_repo.revoke_all(session, email)
            identity_repo.grant_role(session, email,
                                     identity_repo.ACCESS_GRANT_ROLE, region_code=region)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    service.invalidate_region_context()

    who = people.display_name(email) or email
    where = regionmap.region_name(region) if region else "every region"
    events.emit_event("region_granted", actor=user, actor_role=role,
                      summary=f"Gave {who} access to {where}",
                      data={"email": email, "region": region})
    notify.notify(email, f"You now have access to {where}.", actor=user, actor_role=role,
                  data={"region": region})
    return {"ok": True, "person": next(
        (p for p in service.access_people() if p["email"] == email), None)}


@router.delete("/grants/{email}/{region}")
def remove_grant(email: str, region: str, request: Request) -> dict:
    """Take a region back. `region` = "all" removes the all-regions grant.

    Removing the LAST one returns the person to unscoped, which this app treats as
    unrestricted — so the response says so and the page can warn before it
    happens. Quietly widening someone's access is exactly the surprise this
    endpoint should not spring."""
    from resume_screener import events, notify, people, regionmap
    from resume_screener.db.repositories import identity as identity_repo
    from resume_screener.db.session import session_scope

    user, role = _guard(request, "access_edit",
                        "Granting regions is limited to the Super Admin.")
    _require_ready()

    email = email.strip().lower()
    code = None if region.strip().lower() == "all" else region.strip().upper()
    try:
        with session_scope() as session:
            removed = identity_repo.revoke_grant(session, email, code)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not removed:
        raise HTTPException(404, "That person doesn't have access to that region.")
    service.invalidate_region_context()

    # Whether ANY grant survives — asked of the repository, because a person with
    # none left has no row in the listing at all and "no row" would read as False.
    with session_scope() as session:
        now_unscoped = not identity_repo.roles_for_user(session, email)
    person = next((p for p in service.access_people() if p["email"] == email), None)
    who = people.display_name(email) or email
    where = regionmap.region_name(code) if code else "every region"
    events.emit_event("region_revoked", actor=user, actor_role=role,
                      summary=f"Removed {who}'s access to {where}",
                      data={"email": email, "region": code, "now_unscoped": now_unscoped})
    notify.notify(email, f"Your access to {where} was removed.",
                  actor=user, actor_role=role, data={"region": code})
    return {"ok": True, "now_unscoped": now_unscoped, "person": person}
