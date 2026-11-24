"""User + region-scoped-role repository.

`grant_role(region_code=None)` = an all-regions grant (IT admin). `regions_for_user`
returns None to mean "all regions" so callers can distinguish global access from a
specific-region grant.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from resume_screener.db.models.identity import AppUser, UserRegionRole
from resume_screener.db.models.region import Region


def upsert_user(
    session: Session, email: str, *, display_name: str | None = None,
    entra_object_id: str | None = None,
) -> AppUser:
    email = email.strip().lower()
    user = session.scalar(select(AppUser).where(AppUser.email == email))
    if user is None:
        user = AppUser(email=email, display_name=display_name, entra_object_id=entra_object_id)
        session.add(user)
    else:
        if display_name is not None:
            user.display_name = display_name
        if entra_object_id is not None:
            user.entra_object_id = entra_object_id
    session.flush()
    return user


def get_user_by_email(session: Session, email: str) -> AppUser | None:
    return session.scalar(select(AppUser).where(AppUser.email == email.strip().lower()))


def grant_role(
    session: Session, email: str, role: str, *, region_code: str | None = None
) -> UserRegionRole:
    """Grant `role` to a user, scoped to a region (None = all regions). Idempotent."""
    user = upsert_user(session, email)
    region_id = None
    if region_code:
        region = session.scalar(select(Region).where(Region.code == region_code))
        if region is None:
            raise ValueError(f"Unknown region '{region_code}'.")
        region_id = region.id
    existing = session.scalar(
        select(UserRegionRole).where(
            UserRegionRole.user_id == user.id,
            UserRegionRole.region_id == region_id,
            UserRegionRole.role == role,
        )
    )
    if existing is not None:
        return existing
    grant = UserRegionRole(user_id=user.id, region_id=region_id, role=role)
    session.add(grant)
    session.flush()
    return grant


def roles_for_user(session: Session, email: str) -> list[UserRegionRole]:
    user = get_user_by_email(session, email)
    if user is None:
        return []
    return list(session.scalars(select(UserRegionRole).where(UserRegionRole.user_id == user.id)))


def regions_for_user(session: Session, email: str) -> set[str] | None:
    """Region codes the user can access, or None meaning ALL regions (a global grant)."""
    grants = roles_for_user(session, email)
    if any(g.region_id is None for g in grants):
        return None  # all-regions (IT admin)
    region_ids = {g.region_id for g in grants if g.region_id}
    if not region_ids:
        return set()
    rows = session.execute(select(Region.code).where(Region.id.in_(region_ids)))
    return {code for (code,) in rows}


#: What a grant made from the Access page records in the `role` column. Region
#: access and CAPABILITIES are two axes: capabilities come from the person's Entra
#: app roles, and a grant only narrows WHICH requisitions those reach. Writing one
#: constant keeps the unique key effectively (user, region) and stops the column
#: reading as a second, competing source of permission.
ACCESS_GRANT_ROLE = "access"


def list_grants(session: Session) -> list[dict]:
    """Everyone who has been scoped, and to what — one query.

    `region` None on a row means an ALL-REGIONS grant, which is how it is stored."""
    rows = session.execute(
        select(AppUser.email, AppUser.display_name, Region.code, UserRegionRole.role)
        .select_from(UserRegionRole)
        .join(AppUser, AppUser.id == UserRegionRole.user_id)
        .outerjoin(Region, Region.id == UserRegionRole.region_id)
    )
    return [{"email": email, "display_name": name, "region": code, "role": role}
            for email, name, code, role in rows]


def revoke_grant(session: Session, email: str, region_code: str | None) -> int:
    """Remove a person's grant for one region (None = the all-regions grant).

    Removes every row for that (user, region) whatever its `role` string, so a
    legacy grant written by `db seed` under an app-role name is revocable too.
    Returns how many rows went."""
    user = get_user_by_email(session, email)
    if user is None:
        return 0
    region_id = None
    if region_code:
        region = session.scalar(select(Region).where(Region.code == region_code))
        if region is None:
            raise ValueError(f"Unknown region '{region_code}'.")
        region_id = region.id
    rows = list(session.scalars(
        select(UserRegionRole).where(UserRegionRole.user_id == user.id,
                                     UserRegionRole.region_id == region_id)))
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def revoke_all(session: Session, email: str) -> int:
    """Remove every grant a person holds — back to unscoped (which this app treats
    as unrestricted, deliberately: see service._region_context_query)."""
    user = get_user_by_email(session, email)
    if user is None:
        return 0
    rows = roles_for_user(session, email)
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def roles_in_region(session: Session, email: str, region_code: str | None = None) -> set[str]:
    """The roles a user holds in a region — the P27 hook for region-aware capability
    checks. All-regions grants (region_id NULL) always apply; a specific-region grant
    applies only when `region_code` matches. With region_code=None, only the
    all-regions (global) grants count. Enforcement itself is deferred to P27; this
    just makes the roles resolvable per region in the repo layer."""
    grants = roles_for_user(session, email)
    if not grants:
        return set()
    target_id: str | None = None
    if region_code:
        region = session.scalar(select(Region).where(Region.code == region_code))
        target_id = region.id if region else None
    return {
        g.role for g in grants
        if g.region_id is None or (target_id is not None and g.region_id == target_id)
    }
