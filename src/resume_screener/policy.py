"""Jurisdiction Policy Engine (CLAUDE.md §1, §8): load base + overlay packs and
merge them into an EffectivePolicy.

Floor vs dial: overlays may adjust which signals apply and how (caps/params),
obligations, retention and monitoring categories. They can NEVER disable a floor
item (redaction, human decision, evidence, exclusions, logging) — any attempt
fails validation here, and tests pin that behavior.
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import ValidationError

from resume_screener import reqcache
from resume_screener.models import (
    BlockedPattern,
    EffectivePolicy,
    PolicyPack,
    SignalOverride,
)

FLOOR_ITEMS = (
    "redaction",
    "human_decision",
    "evidence_required",
    "excluded_signals",
    "decision_logging",
)


class PolicyError(RuntimeError):
    """Policy problem with a message safe and clear enough for the console/UI."""


def _use_db() -> bool:
    """True when policy packs are read/edited from the database (policy_backend=db)."""
    from resume_screener.config import load_db_settings

    return load_db_settings().policy_backend.strip().lower() == "db"


def load_pack(path: str | Path) -> PolicyPack:
    """A single policy pack as a §4 PolicyPack. Reads the DB row when
    policy_backend=db (the jurisdiction is taken from the file stem, so callers that
    pass a path keep working); otherwise loads + validates the YAML file. Floor
    protection runs either way."""
    if _use_db():
        return _load_pack_db(Path(path).stem)
    return _load_pack_file(path)


def _load_pack_db(code: str) -> PolicyPack:
    from resume_screener.db.repositories import policy as policy_repo
    from resume_screener.db.session import session_scope

    with session_scope() as session:
        pack = policy_repo.load_pack_model(session, code)
    if pack is None:
        raise PolicyError(
            f"No policy pack '{code}' in the database — run `screener db import` once "
            "to backfill packs from policies/."
        )
    _check_floor(pack, is_base=(code == "base"))
    return pack


# Parsed packs, keyed on the RESOLVED path and the file's own state. The DB branch
# has had this since 2026-09-10; the file branch had nothing, so a surface that
# resolves every pack — /api/locations, on the dashboard, Overview AND the
# New-requisition form — read and re-validated base.yaml once per pack (2026-09-15).
# Resolved path, not the relative one: two roots share a relative name, and a
# directory mtime cannot tell them apart (the lesson of the rubric-version cache).
_FILE_PACK_CACHE: dict[str, tuple[tuple[int, int], PolicyPack]] = {}


@functools.lru_cache(maxsize=128)
def _resolved(path_str: str) -> str:
    """`Path.resolve()` is itself a filesystem call, so doing it per load spent the
    read this cache was meant to save. The mapping from a path string to its resolved
    form does not change while the process runs."""
    return str(Path(path_str).resolve())


def _load_pack_file(path: str | Path) -> PolicyPack:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        raise PolicyError(f"Policy pack not found: {p}")
    cache_key = _resolved(str(p))
    state = (st.st_size, st.st_mtime_ns)
    hit = _FILE_PACK_CACHE.get(cache_key)
    if hit is not None and hit[0] == state:
        return hit[1]
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{p} is not valid YAML: {exc}") from exc
    try:
        pack = PolicyPack.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise PolicyError(f"{p} failed validation — {problems}") from exc
    if pack.jurisdiction != p.stem:
        raise PolicyError(
            f"{p}: file is named '{p.stem}' but declares jurisdiction "
            f"'{pack.jurisdiction}' — these must match."
        )
    # Floor protection runs BEFORE the pack is cached, so a cached pack is always
    # one that passed — the same order the DB branch uses.
    _check_floor(pack, is_base=(p.stem == "base"))
    _FILE_PACK_CACHE[cache_key] = (state, pack)
    return pack


def _check_floor(pack: PolicyPack, is_base: bool) -> None:
    if is_base:
        missing = [i for i in FLOOR_ITEMS if pack.floor is None or i not in pack.floor]
        if missing:
            raise PolicyError(
                f"base.yaml must declare every floor item; missing: {', '.join(missing)}."
            )
    disabled = [k for k, v in (pack.floor or {}).items() if not v]
    if disabled:
        raise PolicyError(
            f"Policy pack '{pack.jurisdiction}' tries to disable floor item(s): "
            f"{', '.join(disabled)}. Floor items (redaction, human decision, evidence, "
            "exclusions, logging) can never be turned off by any jurisdiction."
        )


def _resolve_pack_path(jurisdiction: str, policies_dir: Path) -> tuple[Path, list[str]]:
    notes: list[str] = []
    for candidate in (jurisdiction, jurisdiction.upper()):
        path = policies_dir / f"{candidate}.yaml"
        if path.exists():
            return path, notes
    if jurisdiction.upper().startswith("US"):
        fallback = policies_dir / "US-default.yaml"
        if fallback.exists():
            notes.append(
                f"No pack for '{jurisdiction}'; the US-default pack applies "
                "(state/city-specific packs can be added under policies/)."
            )
            return fallback, notes
    available = sorted(p.stem for p in policies_dir.glob("*.yaml") if p.stem != "base")
    raise PolicyError(
        f"No policy pack for jurisdiction '{jurisdiction}'. Available: "
        f"{', '.join(available)} — add policies/{jurisdiction}.yaml or pick one of those."
    )


def _merge_overrides(
    base: dict[str, SignalOverride], overlay: dict[str, SignalOverride]
) -> dict[str, SignalOverride]:
    merged = {sid: ov.model_copy(deep=True) for sid, ov in base.items()}
    for sid, ov in overlay.items():
        if sid not in merged:
            merged[sid] = ov.model_copy(deep=True)
            continue
        current = merged[sid]
        merged[sid] = SignalOverride(
            enabled=ov.enabled if ov.enabled is not None else current.enabled,
            cap_points=ov.cap_points if ov.cap_points is not None else current.cap_points,
            params={**(current.params or {}), **(ov.params or {})} or None,
        )
    return merged


@reqcache.memo(key=lambda jurisdiction, policies_dir="policies": (jurisdiction.strip(), str(policies_dir)))
def resolve_policy(jurisdiction: str, policies_dir: str | Path = "policies") -> EffectivePolicy:
    """base ⊕ overlay for the requisition's job location. Raises PolicyError.

    Loads the base + overlay packs from files or the DB (policy_backend); the merge
    (floor union, obligations union, deep-merged params, retention/monitoring
    inheritance) is shared, so the resolved policy is identical for both backends.
    Resolved once per request per jurisdiction (reqcache): the dashboard resolved
    the SAME pack for every one of 25 requisitions, two YAML parses each time."""
    jurisdiction = jurisdiction.strip()
    if not jurisdiction or jurisdiction.lower() == "base":
        raise PolicyError("A real jurisdiction code is required (e.g. IN, EU, US-NYC).")

    if _use_db():
        base, overlay, notes = _load_base_overlay_db(jurisdiction)
    else:
        base, overlay, notes = _load_base_overlay_file(jurisdiction, Path(policies_dir))
    return _merge_policy(jurisdiction, base, overlay, notes)


def _load_base_overlay_file(
    jurisdiction: str, policies_dir: Path
) -> tuple[PolicyPack, PolicyPack, list[str]]:
    base = _load_pack_file(policies_dir / "base.yaml")
    overlay_path, notes = _resolve_pack_path(jurisdiction, policies_dir)
    overlay = _load_pack_file(overlay_path)
    return base, overlay, notes


# Resolved packs, keyed on the pack table's version stamp. The pack IS governed
# data: it changes only through an approved publish, and reqcache only spans one
# request — so every request was paying five queries (base row + its overrides, the
# jurisdiction list, the overlay row + its overrides) to rebuild something that had
# not moved (2026-09-10). The stamp is one aggregate query and cannot miss a publish.
_DB_PACK_CACHE: dict[tuple, tuple[PolicyPack, PolicyPack, list[str]]] = {}


def invalidate_packs() -> None:
    """Drop the resolved-pack caches. The stamp (DB) and the file state (files) each
    catch another process's publish on their own; this is for a publisher in THIS
    process that wants certainty."""
    _DB_PACK_CACHE.clear()
    _FILE_PACK_CACHE.clear()


@reqcache.memo(key=lambda: "policy.packs")
def _pack_table_stamp() -> tuple:
    """"Have the packs changed?", asked ONCE per request.

    It used to be asked once per JURISDICTION, inside the session each resolve
    opened — so a surface that resolves every pack paid a query per pack to answer
    the same question: `/api/locations` (on the dashboard, Overview AND the
    New-requisition form) spent 7 of its 8 queries here, and it is the packs table,
    which cannot change mid-request. A write clears the request memo through
    `session_scope`, the same mechanism every other reader relies on (2026-09-15)."""
    from resume_screener.db.repositories import policy as policy_repo
    from resume_screener.db.session import session_scope

    with session_scope() as session:
        return policy_repo.table_stamp(session)


def _load_base_overlay_db(jurisdiction: str) -> tuple[PolicyPack, PolicyPack, list[str]]:
    """DB analog of _load_base_overlay_file: load base + the resolved overlay from
    rows, applying the SAME US-default fallback and unknown-jurisdiction error."""
    from resume_screener.db.repositories import policy as policy_repo
    from resume_screener.db.session import session_scope

    notes: list[str] = []
    # Checked BEFORE a session is opened: a cache hit — the ordinary case, since the
    # packs move only when someone publishes — now costs no database work at all.
    stamp = _pack_table_stamp()
    hit = _DB_PACK_CACHE.get((stamp, jurisdiction))
    if hit is not None:
        base, overlay, cached_notes = hit
        return base, overlay, list(cached_notes)
    with session_scope() as session:
        base = policy_repo.load_pack_model(session, "base")
        if base is None:
            raise PolicyError(
                "No base policy pack in the database — run `screener db import` once."
            )
        available = set(policy_repo.list_pack_jurisdictions(session))
        code = next((c for c in (jurisdiction, jurisdiction.upper()) if c in available), None)
        if code is None and jurisdiction.upper().startswith("US") and "US-default" in available:
            code = "US-default"
            notes.append(
                f"No pack for '{jurisdiction}'; the US-default pack applies "
                "(state/city-specific packs can be added under policies/)."
            )
        if code is None:
            raise PolicyError(
                f"No policy pack for jurisdiction '{jurisdiction}'. Available: "
                f"{', '.join(sorted(available))} — add policies/{jurisdiction}.yaml or "
                "pick one of those."
            )
        overlay = policy_repo.load_pack_model(session, code)
    # Floor protection applies to DB-sourced packs too (parity with load_pack). It
    # runs BEFORE the pack is cached, so a cached pack is always one that passed.
    _check_floor(base, is_base=True)
    _check_floor(overlay, is_base=False)
    _DB_PACK_CACHE[(stamp, jurisdiction)] = (base, overlay, list(notes))
    return base, overlay, notes


def _merge_policy(
    jurisdiction: str, base: PolicyPack, overlay: PolicyPack, notes: list[str]
) -> EffectivePolicy:
    floor = dict(base.floor or {})
    floor.update(overlay.floor or {})  # only True values can survive _check_floor

    obligations = list(base.obligations)
    obligations += [o for o in overlay.obligations if o not in obligations]

    exclusions = {e.id: e for e in base.additional_exclusions}
    for extra in overlay.additional_exclusions:
        exclusions.setdefault(extra.id, extra)

    # Blocked patterns: base is the universal FLOOR; the overlay ADDS
    # region-specific ones. An overlay may never redefine a base floor pattern —
    # the floor can only be affirmed (mirrors the floor-item rule above).
    blocked = {p.id: p for p in base.blocked_patterns}
    for p in overlay.blocked_patterns:
        if p.id in blocked and blocked[p.id].floor:
            raise PolicyError(
                f"Policy pack '{overlay.jurisdiction}' redefines base FLOOR blocked "
                f"pattern '{p.id}'. Floor patterns can only be affirmed, never "
                "overridden by a jurisdiction — add a region-specific pattern under "
                "a new id instead."
            )
        blocked[p.id] = p

    for pack in (base, overlay):
        if pack.notes:
            notes.append(pack.notes.strip())

    return EffectivePolicy(
        jurisdiction=jurisdiction,
        pack=overlay.jurisdiction,
        floor=floor,
        signal_overrides=_merge_overrides(base.signal_overrides, overlay.signal_overrides),
        additional_exclusions=list(exclusions.values()),
        blocked_patterns=list(blocked.values()),
        obligations=obligations,
        keep_at_least_days=overlay.keep_at_least_days
        if overlay.keep_at_least_days is not None
        else base.keep_at_least_days,
        purge_by_days=overlay.purge_by_days
        if overlay.purge_by_days is not None
        else base.purge_by_days,
        monitoring_categories=overlay.monitoring_categories
        if overlay.monitoring_categories is not None
        else (base.monitoring_categories or []),
        notes=notes,
    )


# --- Blocked-pattern resolution (the governed floor) --------------------------------


def _load_base_pack(policies_dir: str | Path = "policies") -> PolicyPack | None:
    """The base pack (universal floor), or None if it can't be loaded — callers
    fall back to the code-resident seed so the fairness floor is never absent."""
    try:
        if _use_db():
            return _load_pack_db("base")
        return _load_pack_file(Path(policies_dir) / "base.yaml")
    except PolicyError:
        return None


def base_blocked_patterns(policies_dir: str | Path = "policies") -> list[BlockedPattern]:
    """The universal floor patterns declared in base.yaml (empty if none/absent)."""
    base = _load_base_pack(policies_dir)
    return list(base.blocked_patterns) if base is not None else []


def _all_overlays(policies_dir: str | Path = "policies") -> list[PolicyPack]:
    """Every jurisdiction overlay (all packs except base), from files or the DB."""
    packs: list[PolicyPack] = []
    if _use_db():
        from resume_screener.db.repositories import policy as policy_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            for code in policy_repo.list_pack_jurisdictions(session):
                if code == "base":
                    continue
                model = policy_repo.load_pack_model(session, code)
                if model is not None:
                    packs.append(model)
        return packs
    for path in sorted(Path(policies_dir).glob("*.yaml")):
        if path.stem == "base":
            continue
        try:
            packs.append(_load_pack_file(path))
        except PolicyError:
            continue  # a broken overlay must not sink the union
    return packs


def blocked_patterns_union(policies_dir: str | Path = "policies") -> list[BlockedPattern]:
    """Base ∪ every overlay's blocked patterns (by id; a ``blocked`` severity
    wins over ``warn`` on an id clash).

    This is the STRICTEST-UNION view used to classify a user-global preference
    at authoring time — a pattern blocked in ANY jurisdiction is surfaced — while
    per-req enforcement uses ``resolve_policy(job_location).blocked_patterns``.
    """
    merged: dict[str, BlockedPattern] = {
        p.id: p for p in base_blocked_patterns(policies_dir)
    }
    for pack in _all_overlays(policies_dir):
        for p in pack.blocked_patterns:
            existing = merged.get(p.id)
            if existing is None or (existing.severity == "warn" and p.severity == "blocked"):
                merged[p.id] = p
    return list(merged.values())
