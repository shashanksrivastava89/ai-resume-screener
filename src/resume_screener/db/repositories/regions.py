"""Region / Jurisdiction / Feature-catalog repository.

Idempotent upserts (keyed by natural code/key) so seeding (Phase 7) can run
repeatedly without duplicating rows.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from resume_screener.db.models.region import Feature, Jurisdiction, Region, RegionFeature


def upsert_region(session: Session, code: str, name: str, *, notes: str | None = None) -> Region:
    region = session.scalar(select(Region).where(Region.code == code))
    if region is None:
        region = Region(code=code, name=name, notes=notes)
        session.add(region)
    else:
        region.name = name
        if notes is not None:
            region.notes = notes
    session.flush()
    return region


def get_region_by_code(session: Session, code: str) -> Region | None:
    return session.scalar(select(Region).where(Region.code == code))


def list_regions(session: Session, *, active_only: bool = False) -> list[Region]:
    stmt = select(Region).order_by(Region.code)
    if active_only:
        stmt = stmt.where(Region.is_active.is_(True))
    return list(session.scalars(stmt))


def upsert_jurisdiction(
    session: Session, code: str, name: str, *, law: str | None = None,
    region_code: str | None = None,
) -> Jurisdiction:
    jur = session.scalar(select(Jurisdiction).where(Jurisdiction.code == code))
    region = get_region_by_code(session, region_code) if region_code else None
    if jur is None:
        jur = Jurisdiction(code=code, name=name, law=law,
                           region_id=region.id if region else None)
        session.add(jur)
    else:
        jur.name = name
        if law is not None:
            jur.law = law
        if region is not None:
            jur.region_id = region.id
    session.flush()
    return jur


def list_jurisdictions(session: Session, *, region_code: str | None = None) -> list[Jurisdiction]:
    stmt = select(Jurisdiction).order_by(Jurisdiction.code)
    if region_code:
        region = get_region_by_code(session, region_code)
        stmt = stmt.where(Jurisdiction.region_id == (region.id if region else None))
    return list(session.scalars(stmt))


def upsert_feature(
    session: Session, key: str, name: str, *, description: str | None = None
) -> Feature:
    feat = session.scalar(select(Feature).where(Feature.key == key))
    if feat is None:
        feat = Feature(key=key, name=name, description=description)
        session.add(feat)
    else:
        feat.name = name
        if description is not None:
            feat.description = description
    session.flush()
    return feat


def list_features(session: Session) -> list[Feature]:
    """The whole feature catalog, ordered by key (the /api/features source)."""
    return list(session.scalars(select(Feature).order_by(Feature.key)))


def set_region_feature(session: Session, region_code: str, feature_key: str, enabled: bool) -> None:
    """Turn a feature on/off for a region (feature catalog)."""
    region = get_region_by_code(session, region_code)
    feature = session.scalar(select(Feature).where(Feature.key == feature_key))
    if region is None or feature is None:
        raise ValueError(f"Unknown region '{region_code}' or feature '{feature_key}'.")
    row = session.scalar(
        select(RegionFeature).where(
            RegionFeature.region_id == region.id, RegionFeature.feature_id == feature.id
        )
    )
    if row is None:
        session.add(RegionFeature(region_id=region.id, feature_id=feature.id, enabled=enabled))
    else:
        row.enabled = enabled
    session.flush()


def governing_region_code(session: Session, jurisdiction_code: str) -> str | None:
    """The region that governs a jurisdiction's rules/audit (derived, never stored).

    This is how a job's governing region is resolved from its `job_location`,
    independent of who created it: a US-CA team hiring in India (job_location=IN)
    yields whatever region owns IN. Returns None if the jurisdiction is unknown or
    not yet assigned to a region (caller falls back to the default).
    """
    jur = session.scalar(select(Jurisdiction).where(Jurisdiction.code == jurisdiction_code))
    if jur is None or jur.region_id is None:
        return None
    region = session.get(Region, jur.region_id)
    return region.code if region else None


def region_id_for_jurisdiction(session: Session, jurisdiction_code: str) -> str | None:
    """The governing region's ROW id for a jurisdiction (None if unknown / regions not
    seeded yet). The one place run/audit rows resolve their region from a job's
    jurisdiction, so a run stamped IN lands in whichever region owns IN."""
    code = governing_region_code(session, jurisdiction_code)
    if not code:
        return None
    region = get_region_by_code(session, code)
    return region.id if region else None


def enabled_features(session: Session, region_code: str) -> set[str]:
    """Feature keys explicitly enabled for a region. NOTE: a key missing here means
    'this region states no opinion', NOT 'off' — the flag's own default decides.
    Callers that must tell those apart want `feature_overrides` instead."""
    return {key for key, on in feature_overrides(session, region_code).items() if on}


def feature_overrides(session: Session, region_code: str) -> dict[str, bool]:
    """The region's ACTUAL per-feature overrides: {key: enabled} for the rows that
    exist, and nothing for the features this region never spoke about.

    The distinction is the whole point. A set of enabled keys cannot express "no
    override", so rendering one as a full key→bool matrix silently turns every
    feature without a row OFF for every region — including flags whose declared
    default is ON. That is what killed multi-résumé splitting on the hosted app:
    the flag was imported with default=true, never got a region row, and every
    requisition read it as off."""
    region = get_region_by_code(session, region_code)
    if region is None:
        return {}
    rows = session.execute(
        select(Feature.key, RegionFeature.enabled)
        .join(RegionFeature, RegionFeature.feature_id == Feature.id)
        .where(RegionFeature.region_id == region.id)
    )
    return {key: bool(enabled) for key, enabled in rows}


def all_feature_overrides(session: Session) -> dict[str, dict[str, bool]]:
    """{region_code: {feature key: enabled}} for every region, in ONE query.

    Same contract as feature_overrides per region — a key absent means "no override",
    which is not the same as False (see that docstring). Called per REQUEST by the
    flag catalog, so asking region by region cost two queries per region on every
    request in the app, /api/health included (2026-09-10)."""
    rows = session.execute(
        select(Region.code, Feature.key, RegionFeature.enabled)
        .join(RegionFeature, RegionFeature.region_id == Region.id)
        .join(Feature, RegionFeature.feature_id == Feature.id)
    )
    out: dict[str, dict[str, bool]] = {}
    for code, key, enabled in rows:
        out.setdefault(code, {})[key] = bool(enabled)
    return out
