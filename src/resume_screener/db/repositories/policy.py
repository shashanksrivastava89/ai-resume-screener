"""Policy-pack repository — the DB backend for config-as-data (Phase 6b).

Jurisdiction policy packs (base + IN/EU/US-*/…) move off policies/*.yaml into
`policy_pack` + `policy_signal_override` rows. Packs are VERSIONED: each published
edit inserts a new pack row (UNIQUE(jurisdiction_code, version)); `is_current`
marks the live one and reads take the highest version. Converts to/from the §4
`PolicyPack` so policy.resolve_policy's merge + floor protection stay unchanged —
they operate on the Pydantic model both backends return.

The Phase-1 schema stores everything the seed packs use (floor, obligations,
retention, monitoring categories, notes, per-signal overrides). Pack-level
`additional_exclusions` is not in the schema and no seed pack uses it; the importer
refuses a pack that has one rather than silently dropping it.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.governance import PolicyPack as PolicyPackRow
from resume_screener.db.models.governance import PolicySignalOverride
from resume_screener.models import PolicyPack, SignalOverride


def _pack_contract(pack: PolicyPackRow, overrides) -> PolicyPack:
    return PolicyPack(
        jurisdiction=pack.jurisdiction_code,
        version=pack.version,
        floor=pack.floor,
        signal_overrides={
            o.signal_key: SignalOverride(enabled=o.enabled, cap_points=o.cap_points, params=o.params)
            for o in overrides
        },
        additional_exclusions=[],  # not stored in the Phase-1 schema; no seed pack uses it
        obligations=pack.obligations or [],
        keep_at_least_days=pack.keep_at_least_days,
        purge_by_days=pack.purge_by_days,
        monitoring_categories=pack.monitoring_categories,
        notes=pack.notes,
    )


def has_packs(session: Session) -> bool:
    return bool(session.scalar(select(func.count()).select_from(PolicyPackRow)))


def table_stamp(session: Session) -> tuple:
    """(row count, highest version, latest touch) — a cheap "have the packs changed?".

    A publish appends a row, so the count moves; the version bump moves the second;
    and `updated_at` covers an in-place edit that moved neither. NOT the primary key,
    which is a UUID and orders by nothing meaningful. This is what lets the resolved
    policy be cached ACROSS requests instead of costing five queries per request to
    rebuild governed data that had not moved (2026-09-10)."""
    row = session.execute(
        select(func.count(PolicyPackRow.id),
               func.max(PolicyPackRow.version),
               func.max(PolicyPackRow.updated_at))
    ).one()
    return (int(row[0] or 0), int(row[1] or 0), str(row[2] or ""))


def _current_row(session: Session, jurisdiction: str) -> PolicyPackRow | None:
    """The live pack for a jurisdiction = its highest version (robust regardless of
    the is_current flag, which is kept in sync on write for anyone querying it)."""
    return session.scalars(
        select(PolicyPackRow)
        .where(PolicyPackRow.jurisdiction_code == jurisdiction)
        .order_by(PolicyPackRow.version.desc())
        .limit(1)
    ).first()


def load_pack_model(session: Session, jurisdiction: str) -> PolicyPack | None:
    pack = _current_row(session, jurisdiction)
    if pack is None:
        return None
    overrides = session.scalars(
        select(PolicySignalOverride).where(PolicySignalOverride.policy_pack_id == pack.id)
    )
    return _pack_contract(pack, overrides)


def current_pack_version(session: Session, jurisdiction: str) -> int:
    pack = _current_row(session, jurisdiction)
    return pack.version if pack else 1


def list_pack_jurisdictions(session: Session) -> list[str]:
    """Distinct jurisdiction codes that have a pack, excluding 'base' (mirrors the
    file backend's `policies/*.yaml` glob minus base.yaml)."""
    rows = session.scalars(
        select(PolicyPackRow.jurisdiction_code).distinct()
    )
    return sorted(code for code in rows if code != "base")


def save_pack_version(
    session: Session,
    jurisdiction: str,
    data: dict,
    *,
    version: int,
    published_by: str | None = None,
) -> PolicyPackRow:
    """Insert a new pack version (keeping history) and flip is_current to it. `data`
    is a plain dict — floor / obligations / keep_at_least_days / purge_by_days /
    monitoring_categories / notes + signal_overrides {key: {enabled?, cap_points?,
    params?}} — already validated + floor-checked by governance.py (or straight from
    the importer)."""
    for row in session.scalars(
        select(PolicyPackRow).where(
            PolicyPackRow.jurisdiction_code == jurisdiction, PolicyPackRow.is_current.is_(True)
        )
    ):
        row.is_current = False
    pack = PolicyPackRow(
        jurisdiction_code=jurisdiction, version=version, is_current=True,
        floor=data.get("floor"), obligations=data.get("obligations"),
        keep_at_least_days=data.get("keep_at_least_days"),
        purge_by_days=data.get("purge_by_days"),
        monitoring_categories=data.get("monitoring_categories"),
        notes=data.get("notes"), published_by=published_by,
    )
    session.add(pack)
    session.flush()  # assign pack.id before adding children
    for key, override in (data.get("signal_overrides") or {}).items():
        override = override or {}
        session.add(PolicySignalOverride(
            policy_pack_id=pack.id, signal_key=key,
            enabled=override.get("enabled"), cap_points=override.get("cap_points"),
            params=override.get("params"),
        ))
    session.flush()
    return pack


def import_packs(session: Session, packs: list[PolicyPack]) -> dict:
    """One-time backfill from policies/*.yaml (base included). Skips if packs already
    exist, so `screener db import` is re-runnable."""
    from resume_screener.policy import PolicyError

    if has_packs(session):
        return {"domain": "policies", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    for pack in packs:
        if pack.additional_exclusions:
            raise PolicyError(
                f"Pack '{pack.jurisdiction}' has additional_exclusions, which the DB "
                "backend does not store yet — add a column before importing it."
            )
        data = {
            "floor": pack.floor,
            "obligations": pack.obligations,
            "keep_at_least_days": pack.keep_at_least_days,
            "purge_by_days": pack.purge_by_days,
            "monitoring_categories": pack.monitoring_categories,
            "notes": pack.notes,
            "signal_overrides": {k: v.model_dump() for k, v in pack.signal_overrides.items()},
        }
        save_pack_version(session, pack.jurisdiction, data, version=pack.version, published_by=None)
    return {"domain": "policies", "added": len(packs), "skipped": False, "reason": ""}
