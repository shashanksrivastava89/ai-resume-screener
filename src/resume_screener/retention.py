"""Retention model (P45): the purge queue + legal holds.

VISIBILITY ONLY — nothing here (or anywhere in this program) deletes anything.
It turns the two per-pack retention duties (keep_at_least_days = audit-record
floor, purge_by_days = candidate-PII purge) into a per-requisition schedule that
the Audit Explorer's "PII & retention" tab renders, and lets an admin place/lift
a legal hold that PAUSES a requisition's purge (again: a status change only —
no data is ever removed).

`signals/legal_holds.json` = {req_id: {by, ts, reason}}; writes take the
cross-process file lock and land atomically. apply_hold/release_hold emit
legal_hold_applied/released events onto the one app-event spine (events.py).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from resume_screener.api import service
from resume_screener.events import emit_event
from resume_screener.locking import file_lock

LEGAL_HOLDS = Path("signals/legal_holds.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- legal holds -------------------------------------------------------------------


def load_holds(path: Path = LEGAL_HOLDS) -> dict[str, dict]:
    """Current legal holds, keyed by req_id. Missing/corrupt file → no holds."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_holds(holds: dict[str, dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(holds, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic replace (preserved under the lock)


def apply_hold(req_id: str, *, by: str, reason: str, actor_role: str = "",
               path: Path = LEGAL_HOLDS) -> dict:
    """Place a legal hold on a requisition (pauses its purge). Records the reason —
    it is required and lands in the audit log via the emitted event."""
    if not reason.strip():
        raise ValueError("A reason for the legal hold is required — it is recorded in the audit log.")
    entry = {"by": by, "ts": _now(), "reason": reason.strip()}
    with file_lock(path):
        holds = load_holds(path)
        holds[req_id] = entry
        _write_holds(holds, path)
    emit_event("legal_hold_applied", actor=by, actor_role=actor_role, req_id=req_id,
               summary=f"Legal hold placed — purge paused. {reason.strip()}",
               data={"reason": reason.strip()})
    return entry


def release_hold(req_id: str, *, by: str, actor_role: str = "",
                 path: Path = LEGAL_HOLDS) -> None:
    """Lift a legal hold (purge resumes on schedule). 404-equivalent if none exists."""
    with file_lock(path):
        holds = load_holds(path)
        if holds.pop(req_id, None) is None:
            raise ValueError("No legal hold on this requisition.")
        _write_holds(holds, path)
    emit_event("legal_hold_released", actor=by, actor_role=actor_role, req_id=req_id,
               summary="Legal hold lifted — purge resumes on schedule.", data={})


# --- purge queue -------------------------------------------------------------------


def _due(anchor: datetime | None, days: int | None) -> str | None:
    if anchor is None or not days:
        return None
    return (anchor + timedelta(days=days)).date().isoformat()


def purge_queue(cfg=None) -> list[dict]:
    """Every requisition as a retention row (VISIBILITY ONLY):
    {req_id, title, jurisdiction, pack, closed_at, last_run_at, purge_due
    (closed_at + purge_by_days), keep_audit_until (closed_at + keep_at_least_days),
    status: scheduled|on_hold|awaiting_close, hold?}. The purge clock starts at
    close, so open requisitions are `awaiting_close` with no due date."""
    from resume_screener.policy import PolicyError, resolve_policy

    cfg = cfg or service.config()
    holds = load_holds()
    rows: list[dict] = []
    for req in service.requisitions(cfg):
        life = service.req_lifecycle(req)
        keep_days: int | None = None
        purge_days: int | None = None
        pack: str | None = None
        try:
            eff = resolve_policy(req.job_location, cfg.policy.policies_dir)
            keep_days, purge_days, pack = eff.keep_at_least_days, eff.purge_by_days, eff.pack
        except PolicyError:
            pass
        closed_at = life["closed_at"]
        closed_dt = service._parse_ts(closed_at)
        hold = holds.get(req.req_id)
        if life["status"] != "closed":
            status = "awaiting_close"
        elif hold:
            status = "on_hold"
        else:
            status = "scheduled"
        rows.append({
            "req_id": req.req_id,
            "title": service.display_title(req),
            "jurisdiction": req.job_location,
            "pack": pack,
            "closed_at": closed_at,
            "last_run_at": service.last_activity_utc(req),
            "purge_due": _due(closed_dt, purge_days),
            "keep_audit_until": _due(closed_dt, keep_days),
            "status": status,
            "hold": hold,
        })
    return rows
