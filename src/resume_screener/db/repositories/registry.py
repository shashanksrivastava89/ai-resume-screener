"""Signal-registry repository — the DB backend for config-as-data (Phase 6a).

The registry is the HR/Legal-reviewed catalog of scoring signals + the "never
score" exclusion list. This repo stores it as `signal` + `excluded_signal` rows,
versioned by `registry_version` (one row per published edit, so every run can
stamp exactly which registry it used). It converts to/from the §4 Pydantic
`SignalRegistry` so nothing outside is ORM-aware — `apply_policy` (which turns the
registry into the enabled signal set) is unchanged.

The governance RAILS (locked base exclusions, required job-relatedness on new
rules, cap>0, version bump) live in governance.py and are applied on the Pydantic
model before this repo persists — identical to the file backend. Here we only
store the desired end-state (an upsert by signal id) and append the version row.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.governance import ExcludedSignal, RegistryVersion, Signal
from resume_screener.models import ExcludedSignal as ExcludedContract
from resume_screener.models import Signal as SignalContract
from resume_screener.models import SignalRegistry


def _signal_contract(row: Signal) -> SignalContract:
    return SignalContract(
        id=row.key,
        description=row.description,
        display_name=row.display_name,
        definition=row.definition,
        kind=row.kind,
        direction=row.direction,
        cap_points=row.cap_points,
        enabled_default=row.enabled_default,
        retired=row.retired,
        job_relatedness=row.job_relatedness or "",
        notes=row.notes,
    )


def _excluded_contract(row: ExcludedSignal) -> ExcludedContract:
    return ExcludedContract(id=row.key, reason=row.reason)


def has_registry(session: Session) -> bool:
    return bool(session.scalar(select(func.count()).select_from(Signal)))


def current_version(session: Session) -> int:
    """The highest published registry version (1 if none recorded yet)."""
    return int(session.scalar(select(func.max(RegistryVersion.version))) or 1)


def load_registry_model(session: Session) -> SignalRegistry:
    """Build the §4 SignalRegistry from the rows. Ordered by signal id / exclusion id
    (deterministic; membership, not order, is what apply_policy and the UI use)."""
    signals = [
        _signal_contract(r)
        for r in session.scalars(select(Signal).order_by(Signal.key))
    ]
    excluded = [
        _excluded_contract(r)
        for r in session.scalars(select(ExcludedSignal).order_by(ExcludedSignal.key))
    ]
    return SignalRegistry(version=current_version(session), signals=signals, excluded=excluded)


def update_defaults(session: Session, changes: dict[str, dict]) -> None:
    """The 'Signals' quick-edit: enabled_default / cap_points only (mirrors
    registry.update_registry_defaults on the file backend). Raises on an unknown id;
    validation of the values is the caller's job (the Signal contract bounds them)."""
    rows = {r.key: r for r in session.scalars(select(Signal))}
    unknown = sorted(set(changes) - set(rows))
    if unknown:
        from resume_screener.registry import RegistryError

        raise RegistryError(f"Cannot update unknown signal(s): {', '.join(unknown)}")
    for key, change in changes.items():
        row = rows[key]
        if "enabled_default" in change:
            row.enabled_default = bool(change["enabled_default"])
        if "cap_points" in change:
            row.cap_points = float(change["cap_points"])
    session.flush()


def replace_registry(
    session: Session,
    data: dict,
    *,
    version: int,
    published_by: str | None = None,
    rationale: str | None = None,
    summary: str | None = None,
) -> None:
    """Persist the published end-state (upsert by signal/exclusion id) + a new
    registry_version row. `data` is a plain dict — {"signals": [...], "excluded":
    [...]} of §4-shaped dicts — already validated + rail-checked by governance.py.

    Publish never DELETES a signal (retire keeps it, retired=True) or an exclusion
    (base ones are locked, additions accumulate), so an upsert is complete: the id
    set only ever grows."""
    for spec in data.get("signals", []):
        row = session.scalar(select(Signal).where(Signal.key == spec["id"]))
        if row is None:
            row = Signal(key=spec["id"])
            session.add(row)
        row.display_name = spec.get("display_name")
        row.description = spec.get("description") or ""
        row.definition = spec.get("definition")
        row.kind = spec.get("kind") or "judged"
        row.direction = spec.get("direction") or "both"
        row.cap_points = float(spec.get("cap_points") or 1.0)
        row.enabled_default = bool(spec.get("enabled_default", True))
        row.retired = bool(spec.get("retired", False))
        row.job_relatedness = spec.get("job_relatedness")
        row.notes = spec.get("notes")
    for spec in data.get("excluded", []):
        row = session.scalar(select(ExcludedSignal).where(ExcludedSignal.key == spec["id"]))
        if row is None:
            # A newly published exclusion is never a base-locked one (publish only ADDS
            # to the list; the base 8 are seeded locked at import time).
            row = ExcludedSignal(key=spec["id"], is_base_locked=False)
            session.add(row)
        row.reason = spec.get("reason") or ""
    session.add(RegistryVersion(
        version=version, published_by=published_by, rationale=rationale, summary=summary,
    ))
    session.flush()


def import_registry(
    session: Session, registry: SignalRegistry, *, base_locked_ids: set[str]
) -> dict:
    """One-time backfill from signals/registry.yaml. Skips if signals already exist,
    so `screener db import` is safe to re-run. Stamps is_base_locked on the base
    exclusions (the rail: those can never be removed)."""
    if has_registry(session):
        return {"domain": "registry", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    for s in registry.signals:
        session.add(Signal(
            key=s.id, display_name=s.display_name, description=s.description,
            definition=s.definition, kind=s.kind, direction=s.direction,
            cap_points=s.cap_points, enabled_default=s.enabled_default,
            retired=s.retired, job_relatedness=s.job_relatedness, notes=s.notes,
        ))
    for e in registry.excluded:
        session.add(ExcludedSignal(
            key=e.id, reason=e.reason, is_base_locked=(e.id in base_locked_ids),
        ))
    session.add(RegistryVersion(
        version=registry.version, published_by=None,
        rationale="Imported from signals/registry.yaml",
        summary=f"{len(registry.signals)} signals, {len(registry.excluded)} exclusions",
    ))
    session.flush()
    return {"domain": "registry", "added": len(registry.signals) + len(registry.excluded),
            "skipped": False, "reason": ""}
