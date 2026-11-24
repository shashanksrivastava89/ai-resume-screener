"""File-first feature flags (P37).

NOTE ON THE NAME: the P37 prompt calls this `features.py`, but that module already
exists — it is the engine's computed-features stage (§5.3). To avoid clobbering an
engine file this lives at `feature_flags.py`; everything else follows the prompt.

`signals/features.yaml` is the seed + source of truth for flag definitions. Reads
merge the file (which seeds every key) with the DB region-framework rows (which win
per key they know) so the P49 DB flip is invisible — the read shape is stable across
backends. Writes are file-only for now (ruamel round-trip + cross-process lock +
atomic replace), bump the file `version`, append a governance audit entry
(`action=publish_flags`) and emit a `flag_changed` event.

Two control classes:
  - normal flags: UI-editable. Resolved value = the per-region override if present,
    else the flag's `default`.
  - control:env flags: the value comes ONLY from the named environment variable
    (1/true/on/yes = on). `set_flag`/PUT reject edits (controlled by deployment).
    `careers_autofill` is env-controlled (FEATURE_CAREERS_AUTOFILL). `hm_portal` used to
    be, and stopped being on 2026-09-07: turning the hiring-manager portal on meant an
    app-settings change and a restart, so a SuperAdmin toggles it here like any other
    flag. When it is off, rbac strips the `hm_take` capability everywhere
    (rbac.effective_capabilities).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import yaml as plain_yaml
from ruamel.yaml import YAML

from resume_screener import events, governance
from resume_screener.locking import file_lock
from resume_screener.pathkey import canon

FEATURES_FILE = Path("signals/features.yaml")

# The message the PUT route surfaces (400) when an env-controlled flag is edited.
ENV_CONTROLLED_MESSAGE = (
    "This flag is controlled by deployment (an environment variable) and can't be "
    "changed here."
)

_TRUTHY = {"1", "true", "on", "yes"}


class FeatureError(Exception):
    """Raised for an unknown flag or an attempt to edit an env-controlled one."""


def _env_flag(env_name: str) -> bool:
    return os.environ.get(env_name, "").strip().lower() in _TRUTHY


def _use_flags_db() -> bool:
    """True when editable flags live in the database (FLAGS_BACKEND=db). Reads take
    the DB default + region overrides; writes upsert feature/region_feature. Env-
    controlled flags (hm_portal) stay env-resolved either way. Monkeypatched in tests."""
    from resume_screener.config import load_db_settings

    return load_db_settings().flags_backend.strip().lower() == "db"


def _rt() -> YAML:
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096  # never rewrap the wrapped description strings
    return yaml_rt


_EMPTY: dict = {"version": 0, "features": {}}
# path -> (size, mtime_ns, parsed). The capability check reads this on EVERY request
# — static files and /api/health included — and it was re-read and re-parsed four
# times per call (2026-09-10: 35 ms per capability resolve on a local SSD, each one
# five YAML parses, on a network share five round trips).
# Keyed on the RESOLVED path, never the relative one: FEATURES_FILE is
# "signals/features.yaml" and the tests (and DATA_ROOT) chdir between roots, where
# the seeded copy can carry the SAME size and mtime as the file it was copied from —
# a stale hit across two different files. Exactly the trap that made a published
# rubric invisible to its own process in pass 4.
_FILE_CACHE: dict[str, tuple[int, int, dict]] = {}


def _file_key() -> str:
    try:
        return canon(FEATURES_FILE)
    except OSError:
        return str(FEATURES_FILE)


def _parse_features(path: Path) -> dict:
    try:
        data = plain_yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, plain_yaml.YAMLError):
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    data.setdefault("features", {})
    return data


def _load_file() -> dict:
    """The features.yaml contents as a plain dict (reads only). Missing/corrupt →
    an empty catalog, never a traceback. Parsed once per (size, mtime) — the file is
    replaced atomically by set_flag, so both change on every write."""
    key = _file_key()
    try:
        st = FEATURES_FILE.stat()
    except OSError:
        _FILE_CACHE.pop(key, None)
        return dict(_EMPTY)
    hit = _FILE_CACHE.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    data = _parse_features(FEATURES_FILE)
    _FILE_CACHE[key] = (st.st_size, st.st_mtime_ns, data)
    return data


# What a flag IS (its name, its wording, and who is allowed to change it) ships with
# the code. What a flag is SET TO belongs to the deployment. The two used to travel
# together in one file, and DATA_ROOT seeds that file into an empty root and then
# never overwrites it (dataroot._SEED_FILES) — so a flag added, renamed, or handed
# over to the app by a release stayed frozen as whatever the share was seeded with.
# That is how the hiring-manager portal kept rendering as "controlled by deployment"
# after the release that made it a Super Admin's toggle (2026-09-07).
_DEFINITION_FIELDS = ("name", "description", "control", "env", "scope")

#: Whether a flag can differ per region, declared by the flag rather than assumed.
#:
#: The Feature flags page offered a "By region" row for EVERY non-env flag, which
#: is a promise the code cannot keep for most of them: a per-region override is
#: only ever consulted where a call site has a REQUISITION to take a region from
#: (`flag_enabled(key, region_of_req(req))`). Flags the app reads globally —
#: capability suppression, the MCP connector, the client shell — ignore it
#: entirely, so setting one per region did nothing and said nothing (2026-09-12).
#:
#: MISSING = "requisition", which is the safe direction here even though it is the
#: more permissive one. Narrowing a flag to "app" STRANDS any per-region override
#: it already carries — the region-framework seed (blind_review, audit_export and
#: the two §12 stubs) is exactly that case — while widening one merely offers a
#: control that does nothing, which is the state everything was in before. Every
#: flag this BUILD ships declares its scope outright, and a test pins that, so the
#: default only ever governs a flag that arrived from somewhere else.
SCOPES = ("requisition", "app")
_DEFAULT_SCOPE = "requisition"
APP_SCOPED_MESSAGE = (
    "This flag applies everywhere in the app, so it can't be set for one region.")


_PACKAGED: dict[str, dict] | None = None


def _packaged_features() -> dict[str, dict]:
    """The definitions as this build ships them (the repo's committed features.yaml).

    Read once per process: it lives in the image, so nothing can change it without a
    new container."""
    global _PACKAGED
    if _PACKAGED is not None:
        return _PACKAGED
    from resume_screener.dataroot import REPO_ROOT

    packaged = REPO_ROOT / FEATURES_FILE
    try:
        data = plain_yaml.safe_load(packaged.read_text(encoding="utf-8")) or {}
    except (OSError, plain_yaml.YAMLError):
        _PACKAGED = {}
        return _PACKAGED
    feats = data.get("features") if isinstance(data, dict) else None
    _PACKAGED = {k: (v or {}) for k, v in (feats or {}).items()}
    return _PACKAGED


def _file_features() -> dict[str, dict]:
    """The flag catalog: the deployment's VALUES, under this build's DEFINITIONS.

    A flag the build knows about but the stored file doesn't is added. A flag whose
    control class changed also adopts the build's `default`: while it was
    deployment-controlled nobody could set that value in the app, so there is no
    human decision to preserve — and leaving the stale one would silently turn the
    feature off on the release that handed it over."""
    stored = {k: (v or {}) for k, v in (_load_file().get("features") or {}).items()}
    packaged = _packaged_features()
    if not packaged:
        return stored

    merged: dict[str, dict] = {}
    for key, spec in packaged.items():
        have = dict(stored.get(key) or {})
        changed_hands = have and have.get("control") != spec.get("control")
        for field in _DEFINITION_FIELDS:
            if spec.get(field) is not None:
                have[field] = spec[field]
            else:
                have.pop(field, None)
        if not stored.get(key) or changed_hands:
            # Nobody could set this value while the deployment owned it, so there is
            # no decision to preserve. The next publish writes the reconciled
            # definition back (set_flag), and from then on the stored value governs.
            have["default"] = spec.get("default", have.get("default", False))
        merged[key] = have
    # A flag the deployment has but this build no longer ships stays visible rather
    # than vanishing mid-migration.
    for key, spec in stored.items():
        merged.setdefault(key, spec)
    return merged


# The merged catalog: (file state, monotonic deadline) -> entries. The DB half is a
# handful of rows that change only when someone publishes a flag, so it rides a short
# TTL as the cross-process net, exactly like the requisition list.
_CATALOG_TTL_SECONDS = 15.0
_CATALOG_CACHE: dict[str, tuple[tuple, float, list[dict]]] = {}


def invalidate() -> None:
    """Drop the cached catalog + parsed file. Every writer calls this; the TTL only
    covers a change made by ANOTHER process."""
    _CATALOG_CACHE.clear()
    _FILE_CACHE.clear()


def _file_state() -> tuple:
    try:
        st = FEATURES_FILE.stat()
    except OSError:
        return ()
    return (st.st_size, st.st_mtime_ns)


def _db_features() -> list[dict]:
    """DB region-framework feature rows (empty when unseeded / DB down). Composed
    AROUND service.features_catalog (its internals are out of bounds for P37)."""
    try:
        from resume_screener.api import service

        return service.features_catalog()
    except Exception:  # noqa: BLE001 — unseeded/DB down → file-only, not an error
        return []


def _file_entry(spec: dict, key: str) -> dict:
    entry = {
        "key": key,
        "name": spec.get("name") or key,
        "description": spec.get("description") or "",
        "default": bool(spec.get("default", False)),
        "regions": {str(k): bool(v) for k, v in (spec.get("regions") or {}).items()},
        "scope": spec.get("scope") if spec.get("scope") in SCOPES else _DEFAULT_SCOPE,
        "source": "file",
    }
    if spec.get("control") == "env":
        entry["control"] = "env"
        entry["env"] = spec.get("env") or ""
    return entry


def file_catalog() -> list[dict]:
    """The flag catalog straight from signals/features.yaml (no DB merge) — the
    import source for FLAGS_BACKEND=db. Each entry as `_file_entry` builds it."""
    return [_file_entry(spec, key) for key, spec in _file_features().items()]


def catalog() -> list[dict]:
    """The merged flag catalog — cached; see _catalog_uncached for the real work."""
    key = _file_key()
    state, now = _file_state(), time.monotonic()
    hit = _CATALOG_CACHE.get(key)
    if hit is not None and hit[0] == state and now < hit[1]:
        return hit[2]
    entries = _catalog_uncached()
    _CATALOG_CACHE[key] = (state, now + _CATALOG_TTL_SECONDS, entries)
    return entries


def _catalog_uncached() -> list[dict]:
    """The merged flag catalog (the /api/features source). File order first, then
    any DB-only keys; DB rows win per key they know. Each entry:
    {key, name, description, default, regions:{code:bool}, source, enabled,
     control?, env?} where `enabled` is the resolved global value (env value for
     env-controlled flags, else `default`)."""
    file_feats = _file_features()
    order = list(file_feats.keys())
    entries: dict[str, dict] = {k: _file_entry(spec, k) for k, spec in file_feats.items()}
    # When FLAGS_BACKEND=db the DB is authoritative for a non-env flag's global
    # default too; otherwise the file default wins (unchanged pre-P49 behavior).
    flags_db = _use_flags_db()

    for db in _db_features():
        key = db.get("key")
        if not key:
            continue
        prev = entries.get(key, {})
        if key not in entries:
            order.append(key)
        default = (bool(db.get("default", prev.get("default", False)))
                   if flags_db else bool(prev.get("default", False)))
        merged = {
            "key": key,
            "name": db.get("name") or prev.get("name") or key,
            "description": db.get("description") or prev.get("description") or "",
            "default": default,
            "regions": {str(k): bool(v) for k, v in (db.get("regions") or {}).items()},
            "source": "db",
        }
        # An env-controlled flag stays env-controlled even if a DB row appears for it.
        if prev.get("control") == "env":
            merged["control"] = "env"
            merged["env"] = prev.get("env") or ""
        # Scope is a BUILD definition, like control — whether a flag can differ per
        # region is a fact about the code that reads it, not a value a deployment
        # stores. A DB row that does not carry it must not silently make a
        # requisition-scoped flag app-scoped and strand its overrides.
        #
        merged["scope"] = prev.get("scope") or _DEFAULT_SCOPE
        entries[key] = merged

    out = []
    for key in order:
        entry = entries[key]
        entry["enabled"] = (_env_flag(entry.get("env") or "")
                            if entry.get("control") == "env" else entry["default"])
        out.append(entry)
    return out


def _merged_entry(key: str) -> dict | None:
    return next((e for e in catalog() if e["key"] == key), None)


def flag_enabled(key: str, region: str | None = None) -> bool:
    """Is `key` on (optionally for a region)? Env-controlled flags read ONLY the env
    var; normal flags use the region override if present, else the default. Unknown
    key → False."""
    # Hot path (capability suppression runs on every request): resolve an
    # env-controlled flag straight from the file, with no DB round-trip.
    spec = _file_features().get(key)
    if spec is not None and spec.get("control") == "env":
        return _env_flag(spec.get("env") or "")
    entry = _merged_entry(key)
    if entry is None:
        return False
    if entry.get("control") == "env":
        return _env_flag(entry.get("env") or "")
    # An app-scoped flag ignores the region it was asked about rather than
    # answering from an override nothing else would ever consult.
    if entry.get("scope", "app") == "requisition":
        regions = entry.get("regions") or {}
        if region is not None and str(region) in regions:
            return bool(regions[str(region)])
    return bool(entry["default"])


def set_flag(key: str, region: str | None, enabled: bool, author: str) -> dict:
    """Publish a flag change to the file (ruamel round-trip + lock + atomic replace),
    bump the version, audit it and emit `flag_changed`. `region=None` sets the flag's
    default (global) value; a region code sets that region's override. Rejects unknown
    keys and env-controlled flags."""
    spec = _file_features().get(key)
    if spec is None:
        raise FeatureError(f"Unknown feature flag '{key}'.")
    if spec.get("control") == "env":
        raise FeatureError(ENV_CONTROLLED_MESSAGE)
    scope = spec.get("scope") if spec.get("scope") in SCOPES else _DEFAULT_SCOPE
    if region and scope != "requisition":
        raise FeatureError(APP_SCOPED_MESSAGE)

    if _use_flags_db():
        # DB backend: upsert feature.default_enabled (region=None) or a region_feature
        # override. The file catalog still supplies name/description/order; only the
        # editable value moves. version is the DB's row-count-agnostic monotonic audit
        # version (bumped off the file version so the audit entry still increments).
        from resume_screener.db.repositories import flags as flags_repo
        from resume_screener.db.session import session_scope

        version = int(_load_file().get("version", 0) or 0) + 1
        with session_scope() as session:
            if region is None:
                flags_repo.set_default(session, key, bool(enabled))
            else:
                flags_repo.set_region(session, key, str(region), bool(enabled))
        _audit_flag_change(key, region, enabled, author, version)
        invalidate()   # the value just changed; nothing may serve the old catalog
        return _merged_entry(key) or {}

    yaml_rt = _rt()
    with file_lock(FEATURES_FILE):
        doc = yaml_rt.load(FEATURES_FILE.read_text(encoding="utf-8"))
        feats = doc.get("features") or {}
        entry = feats.get(key)
        if entry is None:
            # A flag this build ships but the stored file predates: write it in, so
            # the deployment's file catches up with the release the first time
            # someone touches it.
            packaged = _packaged_features().get(key)
            if packaged is None:
                raise FeatureError(f"Unknown feature flag '{key}'.")
            feats[key] = entry = dict(packaged)
            doc["features"] = feats
        # Settle the DEFINITION at the same time (2026-09-07). _file_features()
        # reconciles a stale stored definition against the build on every read; once
        # it is written down, "this flag changed hands" stops being true and the
        # value a person just chose is the one that governs.
        for field in _DEFINITION_FIELDS:
            want = (_packaged_features().get(key) or {}).get(field)
            if want is None:
                entry.pop(field, None)
            else:
                entry[field] = want
        if region is None:
            entry["default"] = bool(enabled)
        else:
            regions = entry.get("regions")
            if regions is None:
                entry["regions"] = regions = {}
            regions[str(region)] = bool(enabled)
        version = int(doc.get("version", 0) or 0) + 1
        doc["version"] = version
        tmp = FEATURES_FILE.with_suffix(FEATURES_FILE.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            yaml_rt.dump(doc, handle)
        tmp.replace(FEATURES_FILE)

    _audit_flag_change(key, region, enabled, author, version)
    invalidate()   # the value just changed; nothing may serve the old catalog
    return _merged_entry(key) or {}


def _audit_flag_change(key: str, region: str | None, enabled: bool, author: str,
                       version: int) -> None:
    """The governance audit entry + `flag_changed` event a publish emits — shared by
    the file and DB write paths so the trail is identical either way."""
    scope = region or "all regions"
    state = "on" if enabled else "off"
    summary = f"Feature '{key}' set {state} for {scope}"
    governance.append_audit({
        "action": "publish_flags", "target": key, "author": author, "version": version,
        "summary": summary,
        "rationale": f"Feature flag published: {key} = {state} ({scope}).",
    })
    events.emit_event(
        "flag_changed", actor=author, summary=summary,
        data={"key": key, "region": region, "enabled": bool(enabled), "version": version},
    )
