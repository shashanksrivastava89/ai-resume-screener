"""Feature-flag repository (P49) — the DB backend for signals/features.yaml.

Reuses the region framework's `feature` + `region_feature` tables (Part E): a flag's
global default lives on `feature.default_enabled`; per-region overrides are
`region_feature` rows. Upserts throughout, so `db import` is re-runnable and a
`set_flag` never duplicates. control:env flags (e.g. hm_portal) are NOT stored here —
they stay env-resolved (feature_flags.py reads the file spec for their env mapping).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from resume_screener.db.models.region import Feature, RegionFeature
from resume_screener.db.repositories import regions as regions_repo


def _feature(session: Session, key: str) -> Feature:
    feat = session.scalar(select(Feature).where(Feature.key == key))
    if feat is None:
        feat = Feature(key=key, name=key)
        session.add(feat)
        session.flush()
    return feat


def upsert_flag(session: Session, key: str, *, name: str | None = None,
                description: str | None = None, default_enabled: bool | None = None,
                regions: dict[str, bool] | None = None) -> Feature:
    """Upsert a flag: the feature row (name/description/global default) + any
    per-region overrides (skipping region codes with no Region row yet)."""
    feat = _feature(session, key)
    if name is not None:
        feat.name = name
    if description is not None:
        feat.description = description
    if default_enabled is not None:
        feat.default_enabled = bool(default_enabled)
    session.flush()
    for code, enabled in (regions or {}).items():
        if regions_repo.get_region_by_code(session, code) is not None:
            regions_repo.set_region_feature(session, code, key, bool(enabled))
    return feat


def set_default(session: Session, key: str, enabled: bool) -> None:
    _feature(session, key).default_enabled = bool(enabled)
    session.flush()


def set_region(session: Session, key: str, region_code: str, enabled: bool) -> None:
    _feature(session, key)  # ensure the feature exists
    regions_repo.set_region_feature(session, region_code, key, bool(enabled))


def import_flags(session: Session, entries: list[dict]) -> dict:
    """Backfill the flag catalog (feature.default_enabled + region_feature overrides)
    from the file catalog entries. Idempotent upserts, so it's safe to re-run;
    env-controlled flags are skipped (they carry no DB state)."""
    imported = 0
    for entry in entries:
        key = entry.get("key")
        if not key or entry.get("control") == "env":
            continue
        upsert_flag(session, key, name=entry.get("name"),
                    description=entry.get("description"),
                    default_enabled=bool(entry.get("default", False)),
                    regions={str(k): bool(v) for k, v in (entry.get("regions") or {}).items()})
        imported += 1
    return {"domain": "flags", "added": imported, "skipped": imported == 0,
            "reason": "" if imported else "no editable flags"}
