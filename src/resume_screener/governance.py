"""Governance rails for editable rules & policies (P25; scoring-model merge 2026-07-18).

Two publish surfaces remain: the SCORING MODEL (one full-draft document per
version — knobs + AI signals + never-scored topics, signals/scoring_model.vN.yaml)
and the JURISDICTION PACKS (policies/*.yaml). Editability must never break
auditability, so every published edit here:
  * runs the SAME validation the pipeline uses (schema coherence, pack floor
    protection, the floor-topic acknowledgment rail),
  * bumps the artifact version (scoring model: a NEW immutable vN file),
  * appends {timestamp, author, action, version, diff summary, rationale} to
    signals/audit_log.jsonl, and
  * (re)opens the "pending HR + Legal review" state until an admin signs off
    (publishes that arrived through an approved change proposal don't reopen it
    — the dual review already happened).

Pack writes are comment-preserving (ruamel round-trip) + write-temp-then-rename,
so a bad edit never lands on disk and the curated pack comments survive.

Backend switch (Phase 3): the audit log + review sign-off store is selectable via
`GOVERNANCE_BACKEND` (config.DbSettings.governance_backend). Default "jsonl" keeps
the file-based trail (signals/audit_log.jsonl + review_status.json) so the app runs
with zero DB infra; "db" routes both through the governance repository, region-
scoped, into Postgres. The semantics are IDENTICAL either way — only the storage
moves.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml as plain_yaml
from ruamel.yaml import YAML

from resume_screener import reqcache
from resume_screener.locking import file_lock
from resume_screener.models import PolicyPack
from resume_screener.policy import PolicyError

AUDIT_LOG = Path("signals/audit_log.jsonl")
REVIEW_STATUS = Path("signals/review_status.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rt() -> YAML:
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096  # never rewrap the carefully wrapped description blocks
    return yaml_rt


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# --- audit log + review sign-off ---------------------------------------------------------
#
# Each function keeps its file-based signature (explicit paths) so the existing
# tests and the zero-infra default are untouched; when GOVERNANCE_BACKEND=db it
# routes through the repository instead, region-scoped. `region_id` names a region
# row directly; `jurisdiction` (used by pack publishes) is resolved to its governing
# region inside the session. Both default to None = a global (all-regions) entry.


def _use_db() -> bool:
    """True when the governance trail should be read/written from the database."""
    from resume_screener.config import load_db_settings

    return load_db_settings().governance_backend.strip().lower() == "db"


def _use_policy_db() -> bool:
    """True when policy packs live in the database (policy_backend=db) — an
    INDEPENDENT switch from the governance trail: publish_pack writes the pack
    rows here while the audit entry follows GOVERNANCE_BACKEND."""
    from resume_screener.config import load_db_settings

    return load_db_settings().policy_backend.strip().lower() == "db"


def _resolve_region_id(session, region_id: str | None, jurisdiction: str | None) -> str | None:
    """A region row id for a governance entry: an explicit `region_id` wins; else the
    jurisdiction's governing region (None if regions aren't seeded yet → a global
    entry, which is correct until Phase 7 seeds the region framework)."""
    if region_id is not None or not jurisdiction:
        return region_id
    from resume_screener.db.repositories import regions as regions_repo

    code = regions_repo.governing_region_code(session, jurisdiction)
    if not code:
        return None
    region = regions_repo.get_region_by_code(session, code)
    return region.id if region else None


def append_audit(entry: dict, log_path: Path = AUDIT_LOG, *,
                 region_id: str | None = None, jurisdiction: str | None = None) -> None:
    if _use_db():
        from resume_screener.db.repositories import governance as gov_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            rid = _resolve_region_id(session, region_id, jurisdiction)
            gov_repo.append_audit(session, {"ts": _now(), **entry}, region_id=rid)
        return
    # Cross-process lock (P26): the API process and the screening subprocess/CLI
    # can both append on the hosted single replica.
    with file_lock(log_path), log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": _now(), **entry}, ensure_ascii=False) + "\n")


@reqcache.memo(key=lambda log_path=AUDIT_LOG, *, region_id=None, jurisdiction=None:
               (str(log_path), region_id, jurisdiction))
def load_audit(log_path: Path = AUDIT_LOG, *,
               region_id: str | None = None, jurisdiction: str | None = None) -> list[dict]:
    """The governance audit log. Once per request (reqcache): config_drift consults
    it for every requisition whose scores are stale."""
    if _use_db():
        from resume_screener.db.repositories import governance as gov_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            rid = _resolve_region_id(session, region_id, jurisdiction)
            return gov_repo.load_audit(session, region_id=rid)
    if not log_path.exists():
        return []
    out = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def record_review(reviewers: str, review_date: str, author: str,
                  status_path: Path = REVIEW_STATUS, log_path: Path = AUDIT_LOG, *,
                  region_id: str | None = None, jurisdiction: str | None = None) -> None:
    """An admin records the HR + Legal review (clears the pending banner until the
    next published change) — for a region, or globally (region_id/jurisdiction None)."""
    if not reviewers.strip() or not review_date.strip():
        raise ValueError("Both the reviewers and the review date are required.")
    if _use_db():
        from resume_screener.db.repositories import governance as gov_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            rid = _resolve_region_id(session, region_id, jurisdiction)
            gov_repo.record_review(session, reviewers, review_date, author,
                                   region_id=rid, ts=_now())
        return
    ts = _now()
    with file_lock(status_path):
        _write_atomic(status_path, json.dumps(
            {"ts": ts, "reviewers": reviewers.strip(), "date": review_date.strip(), "by": author}))
    append_audit({"action": "review_signoff", "target": "configuration", "author": author,
                  "rationale": f"HR + Legal review recorded ({review_date.strip()})",
                  "summary": f"reviewers: {reviewers.strip()}"}, log_path)


def last_review(status_path: Path = REVIEW_STATUS, *,
                region_id: str | None = None, jurisdiction: str | None = None) -> dict | None:
    if _use_db():
        from resume_screener.db.repositories import governance as gov_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            rid = _resolve_region_id(session, region_id, jurisdiction)
            return gov_repo.last_review(session, region_id=rid)
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def review_pending(status_path: Path = REVIEW_STATUS, log_path: Path = AUDIT_LOG, *,
                   region_id: str | None = None, jurisdiction: str | None = None) -> bool:
    """True until an admin signs off on the CURRENT configuration — i.e. no review
    yet, or a rules/policy change was published after the last sign-off.

    Decided by ORDER in the append-only audit log, not timestamps: the OS clock's
    coarse resolution can stamp a sign-off and an immediately-following publish with
    the same second, so 'came after' must mean 'later line', not 'later ts'. The DB
    backend orders by the append-only row id, which carries the same guarantee."""
    if _use_db():
        from resume_screener.db.repositories import governance as gov_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            rid = _resolve_region_id(session, region_id, jurisdiction)
            return gov_repo.review_pending(session, region_id=rid)
    entries = load_audit(log_path)
    last_signoff = max((i for i, e in enumerate(entries)
                        if e.get("action") == "review_signoff"), default=-1)
    if last_signoff == -1:
        return True  # never reviewed → pending (internal-evaluation-only default)
    # P51: a publish carrying approved_by went through the second-leader gate —
    # the dual review happened BEFORE it took effect, so it doesn't reopen the
    # banner. Direct/CLI publishes (no approver) still do.
    return any(e.get("action") in ("publish_registry", "publish_pack", "publish_scoring_model")
               and not e.get("approved_by")
               for e in entries[last_signoff + 1:])


def _audit_extras(approved_by: str | None, change_id: str | None) -> dict:
    """Publishes executed by an approved change proposal carry who approved and
    which proposal — review_pending treats those as already-dual-reviewed."""
    extras: dict = {}
    if approved_by:
        extras["approved_by"] = approved_by
    if change_id:
        extras["change_id"] = change_id
    return extras


# --- publish: a jurisdiction policy pack --------------------------------------------------


def _apply_pack_edits(
    data: dict,
    signal_overrides: dict | None,
    obligations: list[str] | None,
    keep_at_least_days: int | None,
    purge_by_days: int | None,
    monitoring_categories: list[str] | None,
    blocked_patterns: list[dict] | None = None,
) -> list[str]:
    """Apply the whole-field pack edits to `data` IN PLACE (backend-agnostic; `data`
    is the ruamel structure or a plain dict), returning the change list. The floor /
    schema rails run in the caller AFTER this."""
    changes: list[str] = []
    if signal_overrides is not None:
        data["signal_overrides"] = signal_overrides
        changes.append("signal settings")
    if blocked_patterns is not None:
        data["blocked_patterns"] = blocked_patterns
        changes.append("blocked patterns")
    if obligations is not None:
        data["obligations"] = obligations
        changes.append("obligations")
    if keep_at_least_days is not None:
        data["keep_at_least_days"] = int(keep_at_least_days)
        changes.append(f"keep≥{int(keep_at_least_days)}d")
    if purge_by_days is not None:
        data["purge_by_days"] = int(purge_by_days)
        changes.append(f"purge→{int(purge_by_days)}d")
    if monitoring_categories is not None:
        data["monitoring_categories"] = monitoring_categories
        changes.append("monitoring categories")
    return changes


def _assert_pack_floor(pack: PolicyPack) -> None:
    """The floor rail on a pack about to be published: an overlay may never turn a
    floor item off (identical to policy._check_floor's overlay branch)."""
    disabled = [k for k, v in (pack.floor or {}).items() if not v]
    if disabled:
        raise PolicyError(f"A policy pack can never disable floor item(s): {', '.join(disabled)}.")


def _assert_pack_signals_known(pack: PolicyPack, policies_dir: str | Path,
                               scoring_dir: str | Path = "signals") -> None:
    """A pack may only dial signals the scoring model actually has.

    Checked at PUBLISH, not only at propose (2026-09-12): a proposal validated
    last week can be approved after a model publish that retired the signal it
    names, and the failure would otherwise land at screening time as a
    RegistryError — a requisition nobody can screen, with nothing on the approval
    to explain it."""
    if pack.jurisdiction == "base" or not pack.signal_overrides:
        return
    from resume_screener import policy as _policy
    from resume_screener.registry import apply_policy, load_registry

    base = _policy._load_base_pack(policies_dir)
    effective = _policy._merge_policy(pack.jurisdiction, base, pack, [])
    apply_policy(load_registry(scoring_dir), effective)


def _assert_blocked_floor(pack: PolicyPack, policies_dir: str | Path) -> None:
    """An overlay may ADD region-specific blocked patterns but can never redefine a
    base FLOOR pattern — mirror of policy._merge_policy's guard, surfaced at
    publish/propose time (before a version bump) rather than only at resolve."""
    if pack.jurisdiction == "base":
        return
    from resume_screener import policy as _policy

    base_floor = {p.id for p in _policy.base_blocked_patterns(policies_dir) if p.floor}
    clash = sorted(p.id for p in pack.blocked_patterns if p.id in base_floor)
    if clash:
        raise PolicyError(
            f"Cannot redefine base FLOOR blocked pattern(s): {', '.join(clash)}. "
            "The universal floor can only be affirmed — add a region-specific "
            "pattern under a new id instead.")


def publish_pack(
    jurisdiction: str,
    *,
    signal_overrides: dict | None = None,
    obligations: list[str] | None = None,
    keep_at_least_days: int | None = None,
    purge_by_days: int | None = None,
    monitoring_categories: list[str] | None = None,
    blocked_patterns: list[dict] | None = None,
    author: str,
    rationale: str,
    approved_by: str | None = None,
    change_id: str | None = None,
    policies_dir: str | Path = "policies",
    log_path: Path = AUDIT_LOG,
) -> int:
    """Edit a jurisdiction pack (overrides/obligations/retention/monitoring) with the
    floor rail; returns the new pack version. base.yaml is not editable here.

    Routes to the DB pack rows when policy_backend=db, else the comment-preserving
    YAML. The rails — schema validation + floor protection + version bump + audit —
    are identical either way."""
    if not rationale.strip():
        raise PolicyError("A reason for the change is required — it is recorded in the audit log.")
    if jurisdiction == "base":
        raise PolicyError("base.yaml carries the floor and is not editable from the UI.")

    if _use_policy_db():
        version, summary = _publish_pack_db(
            jurisdiction, signal_overrides, obligations, keep_at_least_days,
            purge_by_days, monitoring_categories, author, rationale.strip(),
            blocked_patterns=blocked_patterns)
        append_audit({"action": "publish_pack", "target": f"db:policy_pack:{jurisdiction}",
                      "version": version, "author": author, "rationale": rationale.strip(),
                      "summary": summary, **_audit_extras(approved_by, change_id)},
                     log_path, jurisdiction=jurisdiction)
        return version

    policies_dir = Path(policies_dir)
    path = policies_dir / f"{jurisdiction}.yaml"
    if not path.exists():
        raise PolicyError(f"No policy pack at {path}.")

    yaml_rt = _rt()
    # Cross-process lock (P26) around the read-modify-write (same reason as the registry).
    with file_lock(path):
        data = yaml_rt.load(path.read_text(encoding="utf-8"))
        changes = _apply_pack_edits(data, signal_overrides, obligations, keep_at_least_days,
                                    purge_by_days, monitoring_categories, blocked_patterns)
        if not changes:
            raise PolicyError("Nothing changed.")

        data["version"] = int(data.get("version", 1)) + 1

        buffer = io.StringIO()
        yaml_rt.dump(data, buffer)
        new_text = buffer.getvalue()
        # Validate schema AND floor protection BEFORE writing — never leave a bad pack on disk.
        try:
            pack = PolicyPack.model_validate(plain_yaml.safe_load(new_text))
        except Exception as exc:  # noqa: BLE001
            raise PolicyError(f"Edit rejected by validation: {exc}") from exc
        _assert_pack_floor(pack)
        _assert_blocked_floor(pack, policies_dir)  # overlay can't redefine a base floor pattern
        _assert_pack_signals_known(pack, policies_dir)  # the dial names real signals
        _write_atomic(path, new_text)

    append_audit({"action": "publish_pack", "target": f"policies/{jurisdiction}.yaml",
                  "version": data["version"], "author": author, "rationale": rationale.strip(),
                  "summary": f"{jurisdiction}: " + ", ".join(changes),
                  **_audit_extras(approved_by, change_id)}, log_path,
                 jurisdiction=jurisdiction)
    return data["version"]


def _publish_pack_db(
    jurisdiction: str,
    signal_overrides: dict | None,
    obligations: list[str] | None,
    keep_at_least_days: int | None,
    purge_by_days: int | None,
    monitoring_categories: list[str] | None,
    author: str,
    rationale: str,
    blocked_patterns: list[dict] | None = None,
) -> tuple[int, str]:
    """DB path for publish_pack: same edit logic + rails, persisted as a new pack
    version. Returns (new_version, change_summary)."""
    # The DB pack rows use typed columns (no blocked_patterns column yet), so a
    # blocked-pattern edit under the DB backend would validate but not persist —
    # refuse it rather than lose it silently. The universal floor is still
    # enforced under DB via prefcheck's code-seed fallback; regional blocked
    # patterns need the file backend (POLICY_BACKEND=file) until the DB column
    # lands (tracked as a Phase 2 DB follow-up).
    if blocked_patterns is not None:
        raise PolicyError(
            "Editing jurisdiction blocked patterns needs the file backend "
            "(POLICY_BACKEND=file) — DB storage for blocked patterns is a "
            "follow-up. The universal floor stays enforced meanwhile.")
    from resume_screener import policy as _policy_mod
    from resume_screener.db.repositories import policy as policy_repo
    from resume_screener.db.session import session_scope

    with session_scope() as session:
        current = policy_repo.load_pack_model(session, jurisdiction)
        if current is None:
            raise PolicyError(f"No policy pack '{jurisdiction}' in the database.")
        data = {
            "jurisdiction": current.jurisdiction,
            "version": current.version,
            "floor": current.floor,
            "signal_overrides": {k: v.model_dump() for k, v in current.signal_overrides.items()},
            "obligations": list(current.obligations),
            "keep_at_least_days": current.keep_at_least_days,
            "purge_by_days": current.purge_by_days,
            "monitoring_categories": current.monitoring_categories,
            "notes": current.notes,
        }
        changes = _apply_pack_edits(data, signal_overrides, obligations, keep_at_least_days,
                                    purge_by_days, monitoring_categories)
        if not changes:
            raise PolicyError("Nothing changed.")
        new_version = int(data["version"]) + 1
        data["version"] = new_version
        try:  # SAME schema + floor rails as the file path
            pack = PolicyPack.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            raise PolicyError(f"Edit rejected by validation: {exc}") from exc
        _assert_pack_floor(pack)
        _assert_pack_signals_known(pack, "policies")  # same rail as the file path

        save_data = {
            "floor": pack.floor,
            "obligations": pack.obligations,
            "keep_at_least_days": pack.keep_at_least_days,
            "purge_by_days": pack.purge_by_days,
            "monitoring_categories": pack.monitoring_categories,
            "notes": pack.notes,
            "signal_overrides": {k: v.model_dump() for k, v in pack.signal_overrides.items()},
        }
        _policy_mod.invalidate_packs()   # a publish moves the pack; say so at once
        policy_repo.save_pack_version(session, jurisdiction, save_data,
                                      version=new_version, published_by=author)
        summary = f"{jurisdiction}: " + ", ".join(changes)
    return new_version, summary


# --- publish: the scoring model ---------------------------------------------------------
#
# Since the 2026-07-18 merge the model carries the whole scoring configuration
# (knobs + AI signals + never-scored topics), so a publish takes a FULL DRAFT
# document — the Scoring model page stages one draft across all its tabs and
# submits it as one proposal.


def _set_dotted(data: dict, path: str, value) -> None:
    node = data
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):  # also covers ruamel's CommentedMap (a dict)
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def validate_scoring_draft(
    current,
    draft: dict,
    *,
    acknowledged_floor_removals: list[str] | None = None,
):
    """The shared rails for propose-time dry runs AND publish time. Parses the
    draft, enforces the two hard rails beyond the schema, and returns
    (new_model, diff_rows). Raises ScoringModelError with a plain message.

    Rails (everything else is warn-only, per the design mandate):
      * schema coherence (Possible < Strong, credit order, floor < ceiling,
        positive caps, unique ids);
      * removing a FLOOR topic requires its id in acknowledged_floor_removals —
        the explicit legal-exposure acknowledgment the approver signs off on;
      * a NEW signal must start switched off (enabled_default false) — it
        applies to runs only after a follow-up change turns it on;
      * the diff must be non-empty.
    """
    from resume_screener.scoring_model import (
        ScoringModel, ScoringModelError, diff_scoring_models,
    )

    data = dict(draft or {})
    data["version"] = current.version  # the server owns version numbers
    data.setdefault("schema", 1)
    try:
        new_model = ScoringModel.model_validate(data)
    except ScoringModelError:
        raise
    except Exception as exc:  # noqa: BLE001 — clean UI/console message
        raise ScoringModelError(f"Edit rejected by validation: {exc}") from exc

    acknowledged = {str(a) for a in (acknowledged_floor_removals or [])}
    new_ids = {t.id for t in new_model.topics}
    for topic in current.topics:
        if topic.floor and topic.id not in new_ids and topic.id not in acknowledged:
            raise ScoringModelError(
                f"“{topic.name}” is a policy-floor protection — removing it requires "
                "the explicit legal-exposure acknowledgment in the proposal.")

    current_signal_ids = {s.id for s in current.signals}
    for signal in new_model.signals:
        if signal.id not in current_signal_ids and signal.enabled_default:
            name = signal.display_name or signal.id
            raise ScoringModelError(
                f"New signal “{name}” must start switched OFF — turn it on in a "
                "follow-up change once this one is approved.")

    diff = diff_scoring_models(current, new_model)
    if not diff:
        raise ScoringModelError("Nothing changed.")
    return new_model, diff


def publish_scoring_model(
    draft: dict,
    *,
    author: str,
    rationale: str,
    approved_by: str | None = None,
    change_id: str | None = None,
    acknowledged_floor_removals: list[str] | None = None,
    dir: str | Path = "signals",
    log_path: Path = AUDIT_LOG,
) -> int:
    """Publish the next scoring-model version from a FULL draft document:
    validate (validate_scoring_draft rails), stamp version N+1, and write a NEW
    scoring_model.v{N+1}.yaml (old version files are never touched — as-of
    views, drift itemization and version restores read them directly). Returns
    the new version number."""
    from resume_screener.scoring_model import (
        ScoringModelError, canonical_dump, default_scoring_model,
        invalidate_versions, latest_scoring_model_path, load_scoring_model,
    )

    if not rationale.strip():
        raise ScoringModelError(
            "A reason for the change is required — it is recorded in the audit log.")
    if not draft:
        raise ScoringModelError("Nothing changed.")

    dir = Path(dir)
    # One lock for the whole version family (the file names change per version).
    with file_lock(dir / "scoring_model.versions"):
        current = (load_scoring_model(dir) if latest_scoring_model_path(dir) is not None
                   else default_scoring_model())
        new_model, diff = validate_scoring_draft(
            current, draft, acknowledged_floor_removals=acknowledged_floor_removals)
        new_version = int(current.version) + 1
        new_model.version = new_version
        new_text = plain_yaml.safe_dump(
            canonical_dump(new_model), sort_keys=False, allow_unicode=True, width=100)
        _write_atomic(dir / f"scoring_model.v{new_version}.yaml", new_text)
        # A new version FILE is not a rewrite of an existing one, so the file locks'
        # own cache drop doesn't cover it — tell the loader explicitly (the same
        # discipline save_rubric follows). Without this a reader in this process can
        # keep serving the previous model.
        invalidate_versions(dir)

    summary = "; ".join(f"{r['label']}: {r['from']} → {r['to']}" for r in diff)
    append_audit({"action": "publish_scoring_model",
                  "target": str(dir / f"scoring_model.v{new_version}.yaml"),
                  "version": new_version, "author": author, "rationale": rationale.strip(),
                  "summary": summary, **_audit_extras(approved_by, change_id)}, log_path)
    return new_version
