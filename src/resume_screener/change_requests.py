"""Change proposals — the second-leader gate on every scoring-affecting publish.

Editing the scoring model (knobs + AI signals + never-scored topics — one
document since the 2026-07-18 merge) or a jurisdiction pack never publishes
directly: the edit becomes a CHANGE PROPOSAL that a DIFFERENT leader (HR Head /
Legal / Super Admin) must approve before anything takes effect. On approval the
governance publish functions execute — every rail (schema coherence, pack floor
protection, the floor-topic acknowledgment, version bump, audit entry) runs at
apply time, now with `approved_by` on the record.

Store: signals/change_requests.jsonl, event-sourced like preferences.py — one
JSON line per transition, latest line per change_id wins, corrupt lines skipped.
(`signals/proposals.jsonl` is TAKEN by Layer-2 signal proposals — never reuse it.)

Rules:
  * editor ≠ approver (server-enforced, case-insensitive email compare); the
    proposer may WITHDRAW their own pending proposal instead;
  * one pending proposal per (kind, target) — a newer one SUPERSEDES it
    (blocking would deadlock the queue when the proposer is unavailable);
  * propose() DRY-RUNS the payload against the current artifact, so an invalid
    proposal fails fast with the same message it would raise at apply time;
  * out-of-the-usual values become plain-language system_warnings
    ({level: warn|red|info, text, path}) that travel with the record to the
    approver — warned, never blocked; "red" warnings carry legal exposure the
    approver explicitly acknowledges;
  * approve 409s if the target's version moved since the proposal (belt and
    suspenders; supersede semantics make this nearly unreachable).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock

CHANGES_LOG = Path("signals/change_requests.jsonl")

KINDS = ("scoring_model", "policy_pack")
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DECLINED = "declined"
STATUS_SUPERSEDED = "superseded"
STATUS_WITHDRAWN = "withdrawn"


class ChangeRequestError(RuntimeError):
    """Validation problem — plain message, maps to HTTP 400."""


class SelfApprovalError(RuntimeError):
    """The proposer tried to approve their own change — maps to HTTP 403."""


class ChangeConflictError(RuntimeError):
    """Not pending / underlying version moved — maps to HTTP 409."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def required_capability(kind: str) -> str:
    """The capability needed to PROPOSE a change of this kind (rbac.yaml grants)."""
    return "scoring_edit" if kind == "scoring_model" else "rules_edit"


# --- the store (preferences.py idioms: locked append, latest-line-per-id fold) --------


def _append(record: dict, log_path: str | Path) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _fold(log_path: str | Path) -> dict[str, dict]:
    path = Path(log_path)
    if not path.exists():
        return {}
    folded: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("change_id"):
            folded[rec["change_id"]] = rec
    return folded


def list_changes(status: str | None = None, kind: str | None = None,
                 log_path: str | Path = CHANGES_LOG) -> list[dict]:
    """Latest state of every change, newest first."""
    records = list(_fold(log_path).values())
    if status:
        records = [r for r in records if r.get("status") == status]
    if kind:
        records = [r for r in records if r.get("kind") == kind]
    return sorted(records, key=lambda r: r.get("proposed_at", ""), reverse=True)


def get_change(change_id: str, log_path: str | Path = CHANGES_LOG) -> dict | None:
    return _fold(log_path).get(change_id)


def pending_count(log_path: str | Path = CHANGES_LOG) -> int:
    return len(list_changes(status=STATUS_PENDING, log_path=log_path))


# --- dry runs: summary + warnings + base version ---------------------------------------


def _days(value: object) -> str:
    return f"{value} days" if value is not None else "not set"


def _signal_names(scoring_dir: str | Path = "signals") -> dict[str, str]:
    from resume_screener.registry import load_registry

    try:
        return {s.id: (s.display_name or s.id) for s in load_registry(scoring_dir).signals}
    except Exception:  # noqa: BLE001 — a diff must never fail on a label
        return {}


def _model_caps(scoring_dir: str | Path = "signals") -> dict[str, float]:
    from resume_screener.registry import load_registry

    try:
        return {s.id: s.cap_points for s in load_registry(scoring_dir).signals}
    except Exception:  # noqa: BLE001
        return {}


def _cap(value, model_cap) -> str:
    """A cap as an approver reads it. Absent = the model's own number; zero means
    the signal does not fire here at all, which "0" alone does not say."""
    if value is None:
        return f"the model's ±{model_cap:g}" if model_cap is not None else "the model's cap"
    if float(value) == 0:
        return "off here"
    return f"±{float(value):g}"


def _on_off(value) -> str:
    if value is None:
        return "the model's setting"
    return "on here" if value else "off here"


def _params(value) -> str:
    items = sorted((value or {}).items())
    if not items:
        return "the model's settings"
    return " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in items)


def _pack_diff(old: dict, new: dict, jurisdiction: str) -> list[dict]:
    """Field-level from→to rows for a jurisdiction-pack proposal (2026-07-19 —
    approvers previously saw only labels like 'obligations', with nothing to
    compare). Same {path, label, from, to} shape the scoring diff uses."""
    rows: list[dict] = []
    if old.get("keep_at_least_days") != new.get("keep_at_least_days"):
        rows.append({"path": "keep_at_least_days", "label": f"{jurisdiction}: audit records kept at least",
                     "from": _days(old.get("keep_at_least_days")), "to": _days(new.get("keep_at_least_days"))})
    if old.get("purge_by_days") != new.get("purge_by_days"):
        rows.append({"path": "purge_by_days", "label": f"{jurisdiction}: candidate PII purged after",
                     "from": _days(old.get("purge_by_days")), "to": _days(new.get("purge_by_days"))})
    for field, label in (("obligations", "obligation"), ("monitoring_categories", "monitoring category")):
        old_items = list(old.get(field) or [])
        new_items = list(new.get(field) or [])
        for item in new_items:
            if item not in old_items:
                rows.append({"path": field, "label": f"{jurisdiction}: {label} added",
                             "from": "—", "to": str(item)})
        for item in old_items:
            if item not in new_items:
                rows.append({"path": field, "label": f"{jurisdiction}: {label} removed",
                             "from": str(item), "to": "—"})
    # Signal overrides: the regional DIAL. Named by their display name and priced
    # against the model's own cap, because "language_evidence: 2 → 3" tells an
    # approver neither what it is nor that 2 was the model's number (2026-09-12).
    old_ov = dict(old.get("signal_overrides") or {})
    new_ov = dict(new.get("signal_overrides") or {})
    for sid in sorted(set(old_ov) | set(new_ov)):
        a, b = old_ov.get(sid) or {}, new_ov.get(sid) or {}
        if a == b:
            continue
        name = _signal_names().get(sid, sid.replace("_", " ").capitalize())
        model_cap = _model_caps().get(sid)
        if a.get("cap_points") != b.get("cap_points"):
            rows.append({
                "path": f"signal_overrides.{sid}.cap_points",
                "label": f"{jurisdiction}: “{name}” may move a score by up to",
                "from": _cap(a.get("cap_points"), model_cap),
                "to": _cap(b.get("cap_points"), model_cap),
            })
        if a.get("enabled") != b.get("enabled"):
            rows.append({
                "path": f"signal_overrides.{sid}.enabled",
                "label": f"{jurisdiction}: “{name}”",
                "from": _on_off(a.get("enabled")), "to": _on_off(b.get("enabled")),
            })
        if (a.get("params") or {}) != (b.get("params") or {}):
            rows.append({
                "path": f"signal_overrides.{sid}.params",
                "label": f"{jurisdiction}: “{name}” market settings",
                "from": _params(a.get("params")), "to": _params(b.get("params")),
            })

    # Blocked patterns: added / removed / severity-changed, keyed by id.
    old_bp = {p["id"]: p for p in (old.get("blocked_patterns") or [])}
    new_bp = {p["id"]: p for p in (new.get("blocked_patterns") or [])}
    for pid in sorted(set(old_bp) | set(new_bp)):
        a, b = old_bp.get(pid), new_bp.get(pid)
        name = (b or a).get("name", pid)
        if a is None:
            rows.append({"path": f"blocked_patterns.{pid}", "label": f"{jurisdiction}: blocked pattern added",
                         "from": "—", "to": f"{name} ({b.get('severity', 'blocked')})"})
        elif b is None:
            rows.append({"path": f"blocked_patterns.{pid}", "label": f"{jurisdiction}: blocked pattern removed",
                         "from": f"{name} ({a.get('severity', 'blocked')})", "to": "—"})
        elif a.get("severity") != b.get("severity"):
            rows.append({"path": f"blocked_patterns.{pid}.severity",
                         "label": f"{jurisdiction}: “{name}” severity",
                         "from": str(a.get("severity")), "to": str(b.get("severity"))})
    old_ov = old.get("signal_overrides") or {}
    new_ov = new.get("signal_overrides") or {}
    for sid in sorted(set(old_ov) | set(new_ov)):
        a, b = old_ov.get(sid) or {}, new_ov.get(sid) or {}
        name = sid.replace("_", " ").capitalize()
        if a.get("enabled") != b.get("enabled"):
            rows.append({"path": f"signal_overrides.{sid}.enabled", "label": f"{jurisdiction}: “{name}”",
                         "from": "on" if a.get("enabled", True) else "off",
                         "to": "on" if b.get("enabled", True) else "off"})
        if a.get("cap_points") != b.get("cap_points"):
            rows.append({"path": f"signal_overrides.{sid}.cap_points", "label": f"{jurisdiction}: “{name}” cap",
                         "from": str(a.get("cap_points", "pack default")),
                         "to": str(b.get("cap_points", "pack default"))})
        if (a.get("params") or {}) != (b.get("params") or {}):
            pa, pb = a.get("params") or {}, b.get("params") or {}
            for key in sorted(set(pa) | set(pb)):
                if pa.get(key) != pb.get(key):
                    rows.append({"path": f"signal_overrides.{sid}.params.{key}",
                                 "label": f"{jurisdiction}: “{name}” · {key.replace('_', ' ')}",
                                 "from": str(pa.get(key, "—")), "to": str(pb.get(key, "—"))})
    return rows


def _signal_warnings(jurisdiction: str, overrides: dict, scoring_dir: str | Path) -> list[dict]:
    """Plain notes for the approver about what this pack asks of the model.

    None of these BLOCK. A pack raising a cap above the model's is allowed on
    purpose — India does it for language evidence — but it is the one direction a
    reader would not assume, so it is said out loud rather than left in a diff."""
    from resume_screener.registry import load_registry

    try:
        registry = load_registry(scoring_dir)
    except Exception:  # noqa: BLE001 — validation happens elsewhere; this is commentary
        return []
    by_id = {s.id: s for s in registry.signals}
    out: list[dict] = []
    for sid, override in (overrides or {}).items():
        signal = by_id.get(sid)
        if signal is None:
            continue  # apply_policy refuses it below; no need to editorialise
        name = signal.display_name or sid
        cap = (override or {}).get("cap_points")
        enabled = (override or {}).get("enabled")
        if cap is not None and float(cap) > signal.cap_points:
            out.append({"level": "amber", "path": f"signal_overrides.{sid}.cap_points",
                        "text": f"“{name}” could move a score by up to ±{float(cap):g} for "
                                f"{jurisdiction} requisitions — the company-wide model caps "
                                f"it at ±{signal.cap_points:g}. Allowed, and worth a look."})
        elif cap is not None and float(cap) == 0:
            out.append({"level": "info", "path": f"signal_overrides.{sid}.cap_points",
                        "text": f"“{name}” will not fire for {jurisdiction} requisitions "
                                "(its cap is set to zero)."})
        if enabled is True and not signal.enabled_default:
            out.append({"level": "amber", "path": f"signal_overrides.{sid}.enabled",
                        "text": f"“{name}” is OFF in the company-wide model; this turns it "
                                f"on for {jurisdiction} only."})
        elif enabled is False:
            out.append({"level": "info", "path": f"signal_overrides.{sid}.enabled",
                        "text": f"“{name}” will not fire for {jurisdiction} requisitions."})
    return out


def _dry_run_pack(jurisdiction: str, payload: dict, policies_dir: str | Path,
                  scoring_dir: str | Path = "signals") -> tuple[list[str], list[dict], int, list[dict]]:
    from resume_screener import governance
    from resume_screener.models import PolicyPack
    from resume_screener.policy import PolicyError, load_pack

    pack = load_pack(Path(policies_dir) / f"{jurisdiction}.yaml")
    data = {
        "jurisdiction": pack.jurisdiction, "version": pack.version, "floor": pack.floor,
        "signal_overrides": {k: v.model_dump() for k, v in pack.signal_overrides.items()},
        "blocked_patterns": [p.model_dump() for p in pack.blocked_patterns],
        "obligations": list(pack.obligations),
        "keep_at_least_days": pack.keep_at_least_days,
        "purge_by_days": pack.purge_by_days,
        "monitoring_categories": pack.monitoring_categories,
        "notes": pack.notes,
    }
    before = json.loads(json.dumps(data))  # deep snapshot for the from→to diff
    changes = governance._apply_pack_edits(
        data,
        payload.get("signal_overrides"),
        payload.get("obligations"),
        payload.get("keep_at_least_days"),
        payload.get("purge_by_days"),
        payload.get("monitoring_categories"),
        payload.get("blocked_patterns"),
    )
    if not changes:
        raise ChangeRequestError("Nothing changed.")
    data["version"] = pack.version + 1
    try:
        new_pack = PolicyPack.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        raise ChangeRequestError(f"Edit rejected by validation: {exc}") from exc
    try:  # floor rails -> a clean 400 on the proposal, not a 500
        governance._assert_pack_floor(new_pack)
        governance._assert_blocked_floor(new_pack, policies_dir)
    except PolicyError as exc:
        raise ChangeRequestError(str(exc)) from exc
    if payload.get("signal_overrides") is not None:
        # The dial has to make sense to the ENGINE before it is proposed: an
        # override naming a signal the model does not have is a requisition nobody
        # can screen, and apply_policy — the call a run makes — is the only honest
        # judge of that (2026-09-12). Its RegistryError carries the right words.
        from resume_screener.policy import _load_base_pack, _merge_policy
        from resume_screener.registry import RegistryError, apply_policy, load_registry

        try:
            base = _load_base_pack(policies_dir)
            effective = _merge_policy(jurisdiction, base, new_pack, [])
            apply_policy(load_registry(scoring_dir), effective)
        except RegistryError as exc:
            raise ChangeRequestError(str(exc)) from exc
        except PolicyError as exc:
            raise ChangeRequestError(str(exc)) from exc

    diff = _pack_diff(before, data, jurisdiction)
    summary = [f"{r['label']}: {r['from']} → {r['to']}" for r in diff] \
        or [f"{jurisdiction}: {c}" for c in changes]
    warnings = _signal_warnings(jurisdiction, payload.get("signal_overrides") or {}, scoring_dir)
    return summary, warnings, pack.version, diff


def _dry_run_scoring(payload: dict, scoring_dir: str | Path) -> tuple[list[str], list[dict], int, list[dict]]:
    from resume_screener import governance
    from resume_screener.scoring_model import (
        ScoringModelError, load_scoring_model, system_warnings,
    )

    draft = payload.get("model") or {}
    if not draft:
        raise ChangeRequestError("Nothing changed.")
    old = load_scoring_model(scoring_dir)
    try:
        new_model, diff = governance.validate_scoring_draft(
            old, draft,
            acknowledged_floor_removals=payload.get("acknowledged_floor_removals"))
    except ScoringModelError as exc:
        raise ChangeRequestError(str(exc)) from exc
    summary = [f"{r['label']}: {r['from']} → {r['to']}" for r in diff]
    warnings = system_warnings(old, new_model)
    return summary, warnings, old.version, diff


def _dry_run(kind: str, target: str, payload: dict, *,
             policies_dir: str | Path, scoring_dir: str | Path) -> tuple[list[str], list[dict], int, list[dict]]:
    if kind == "policy_pack":
        return _dry_run_pack(target.split(":", 1)[1], payload, policies_dir, scoring_dir)
    return _dry_run_scoring(payload, scoring_dir)


def _current_version(kind: str, target: str, *, policies_dir: str | Path,
                     scoring_dir: str | Path) -> int:
    if kind == "policy_pack":
        from resume_screener.policy import load_pack

        return load_pack(Path(policies_dir) / f"{target.split(':', 1)[1]}.yaml").version
    from resume_screener.scoring_model import load_scoring_model

    return load_scoring_model(scoring_dir).version


# --- the workflow ----------------------------------------------------------------------


def propose_change(
    kind: str,
    target: str,
    payload: dict,
    *,
    rationale: str,
    author: str,
    author_role: str = "",
    log_path: str | Path = CHANGES_LOG,
    policies_dir: str | Path = "policies",
    scoring_dir: str | Path = "signals",
) -> dict:
    """Record a pending change proposal (dry-run validated). Supersedes any
    pending proposal for the same (kind, target). Returns the new record.

    Payloads: kind "scoring_model" carries {"model": <full draft document>,
    "acknowledged_floor_removals": [topic ids]}; kind "policy_pack" carries the
    whole-field pack edits (target "pack:<jurisdiction>")."""
    if kind not in KINDS:
        raise ChangeRequestError(f"Unknown change kind '{kind}'.")
    if not rationale.strip():
        raise ChangeRequestError(
            "A reason for the change is required — the approver reads it, and it "
            "is recorded in the audit log.")
    if kind == "policy_pack" and not (target.startswith("pack:") and target.split(":", 1)[1]):
        raise ChangeRequestError("A pack proposal's target must be 'pack:<jurisdiction>'.")

    summary, warnings, base_version, diff = _dry_run(
        kind, target, payload,
        policies_dir=policies_dir, scoring_dir=scoring_dir)

    superseded_id = None
    for existing in list_changes(status=STATUS_PENDING, log_path=log_path):
        if existing["kind"] == kind and existing["target"] == target:
            superseded_id = existing["change_id"]
            break

    record = {
        "change_id": uuid.uuid4().hex[:12],
        "kind": kind,
        "target": target,
        "base_version": base_version,
        "payload": payload,
        "summary": summary,
        "diff": diff,
        "system_warnings": warnings,
        "rationale": rationale.strip(),
        "proposed_by": author,
        "proposed_by_role": author_role,
        "proposed_at": _now(),
        "status": STATUS_PENDING,
        "decided_by": None,
        "decided_at": None,
        "decline_note": None,
        "superseded_by": None,
        "resulting_version": None,
        "action": "propose",
        "ts": _now(),
    }
    if superseded_id:
        old = get_change(superseded_id, log_path)
        _append({**old, "action": "supersede", "status": STATUS_SUPERSEDED,
                 "superseded_by": record["change_id"], "ts": _now()}, log_path)
    _append(record, log_path)

    from resume_screener.governance import append_audit

    append_audit({
        "action": "propose_change", "target": target, "author": author,
        "rationale": rationale.strip(), "summary": "; ".join(summary),
        "change_id": record["change_id"],
    })
    return record


def _target_label(record: dict) -> str:
    """Plain words for a proposal target ('the scoring model' / 'the IN pack')."""
    target = str(record.get("target", ""))
    if record.get("kind") == "policy_pack" and ":" in target:
        return f"the {target.split(':', 1)[1]} jurisdiction pack"
    return "the scoring model"


def approve_change(
    change_id: str,
    *,
    approver: str,
    approver_role: str = "",
    log_path: str | Path = CHANGES_LOG,
    policies_dir: str | Path = "policies",
    scoring_dir: str | Path = "signals",
) -> dict:
    """Approve a pending change: the second-leader gate. Executes the underlying
    governance publish (all rails run here) and records the result."""
    from resume_screener import governance

    record = get_change(change_id, log_path)
    if record is None:
        raise ChangeRequestError("Unknown change.")
    if record["status"] != STATUS_PENDING:
        raise ChangeConflictError("This change is no longer pending.")
    if record["proposed_by"].strip().lower() == approver.strip().lower():
        raise SelfApprovalError(
            "You proposed this change — another leader has to approve it.")
    current = _current_version(record["kind"], record["target"],
                               policies_dir=policies_dir, scoring_dir=scoring_dir)
    if current != record["base_version"]:
        raise ChangeConflictError(
            "The underlying configuration changed since this was proposed — ask "
            "the editor to propose it again on the current version.")

    payload = record["payload"] or {}
    if record["kind"] == "policy_pack":
        version = governance.publish_pack(
            record["target"].split(":", 1)[1],
            signal_overrides=payload.get("signal_overrides"),
            obligations=payload.get("obligations"),
            keep_at_least_days=payload.get("keep_at_least_days"),
            purge_by_days=payload.get("purge_by_days"),
            monitoring_categories=payload.get("monitoring_categories"),
            blocked_patterns=payload.get("blocked_patterns"),
            author=record["proposed_by"], rationale=record["rationale"],
            approved_by=approver, change_id=change_id, policies_dir=policies_dir,
        )
    else:
        version = governance.publish_scoring_model(
            payload.get("model") or {},
            author=record["proposed_by"], rationale=record["rationale"],
            approved_by=approver, change_id=change_id,
            acknowledged_floor_removals=payload.get("acknowledged_floor_removals"),
            dir=scoring_dir,
        )

    updated = {**record, "action": "approve", "status": STATUS_APPROVED,
               "decided_by": approver, "decided_at": _now(),
               "resulting_version": version, "ts": _now()}
    _append(updated, log_path)
    # 2026-07-19: the proposer learns the outcome (bell notification).
    from resume_screener import names
    from resume_screener.notify import notify
    notify(record["proposed_by"],
           f"Your change to {_target_label(record)} was approved by "
           f"{names.display_name(approver)} — v{version} is now in effect.",
           actor=approver, data={"kind": "change_approved", "change_id": change_id})
    return updated


def decline_change(
    change_id: str,
    *,
    note: str,
    approver: str,
    approver_role: str = "",
    log_path: str | Path = CHANGES_LOG,
) -> dict:
    """Return to the proposer with a required note — they learn why, audit keeps it."""
    from resume_screener.governance import append_audit

    if not note.strip():
        raise ChangeRequestError("A note explaining the decline is required.")
    record = get_change(change_id, log_path)
    if record is None:
        raise ChangeRequestError("Unknown change.")
    if record["status"] != STATUS_PENDING:
        raise ChangeConflictError("This change is no longer pending.")
    updated = {**record, "action": "decline", "status": STATUS_DECLINED,
               "decided_by": approver, "decided_at": _now(),
               "decline_note": note.strip(), "ts": _now()}
    _append(updated, log_path)
    append_audit({
        "action": "decline_change", "target": record["target"], "author": approver,
        "rationale": note.strip(), "summary": "; ".join(record.get("summary") or []),
        "change_id": change_id,
    })
    # 2026-07-19: the proposer learns the outcome AND the note (bell notification
    # — before this, a declined proposal simply vanished from their queue).
    from resume_screener import names
    from resume_screener.notify import notify
    notify(record["proposed_by"],
           f"Your change to {_target_label(record)} was declined by "
           f"{names.display_name(approver)}: {note.strip()}",
           actor=approver, data={"kind": "change_declined", "change_id": change_id})
    return updated


def withdraw_change(
    change_id: str,
    *,
    author: str,
    log_path: str | Path = CHANGES_LOG,
) -> dict:
    """The proposer pulls their own pending proposal — nothing changed, the
    record stays in the trail."""
    from resume_screener.governance import append_audit

    record = get_change(change_id, log_path)
    if record is None:
        raise ChangeRequestError("Unknown change.")
    if record["status"] != STATUS_PENDING:
        raise ChangeConflictError("This change is no longer pending.")
    if record["proposed_by"].strip().lower() != author.strip().lower():
        raise SelfApprovalError("Only the proposer can withdraw a proposal.")
    updated = {**record, "action": "withdraw", "status": STATUS_WITHDRAWN,
               "decided_by": author, "decided_at": _now(), "ts": _now()}
    _append(updated, log_path)
    append_audit({
        "action": "withdraw_change", "target": record["target"], "author": author,
        "rationale": "Withdrawn by the proposer — nothing changed.",
        "summary": "; ".join(record.get("summary") or []),
        "change_id": change_id,
    })
    return updated
