"""Checklist approval state (P43): `jds/<stem>.approval.json`.

When a recruiter publishes a checklist whose text the deterministic critic
(prefcheck) flags as WARN, that version can't be *used* until the HR Head signs
off. This module tracks one small record per requisition, keyed to the rubric
version it applies to:

    {version, status: none|pending|approved, flagged_requirement_ids[],
     requested_by?, requested_at?, approved_by?, approved_at?}

- `record_publish` runs on every save: a version with flagged rows resets the
  record to `none` (fresh approval needed); a clean version clears it.
- `request_approval` (recruiter) → pending; `approve` (HR Head) → approved.

Writes are atomic (temp + os.replace) under a cross-process file lock. The
record is a sidecar beside the JD, so it survives candidate-data deletion and
never enters the governance audit log (req/app spine only).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock

SUFFIX = ".approval.json"


def _now() -> str:
    """UTC ISO stamp — a module function so tests can monkeypatch it."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def approval_path(jd_path: str | Path) -> Path:
    jd = Path(jd_path)
    return jd.with_name(jd.stem + SUFFIX)


def _default(version: int = 0) -> dict:
    return {
        "version": version,
        "status": "none",
        "flagged_requirement_ids": [],
        "requested_by": None,
        "requested_at": None,
        "approved_by": None,
        "approved_at": None,
    }


def load_approval(jd_path: str | Path) -> dict:
    """The stored record, or a fresh default when none exists / it's corrupt."""
    path = approval_path(jd_path)
    if not path.exists():
        return _default()
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return _default()
    return rec if isinstance(rec, dict) else _default()


def _write(jd_path: str | Path, record: dict) -> dict:
    path = approval_path(jd_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return record


def record_publish(jd_path: str | Path, *, version: int, flagged_ids: list[str]) -> dict:
    """Reconcile the record after a rubric save. A version WITH flagged rows
    resets approval to `none` (a fresh publish always needs fresh sign-off); a
    clean version clears the record to `none` with no flags."""
    record = _default(version)
    record["flagged_requirement_ids"] = list(flagged_ids)
    return _write(jd_path, record)


def request_approval(jd_path: str | Path, *, version: int, requested_by: str) -> dict | None:
    """Recruiter sends the flagged version for HR-Head approval. Returns the
    updated record, or None if it's already pending/approved (no re-request)."""
    record = load_approval(jd_path)
    if record.get("version") != version or record.get("status") != "none":
        return None
    record.update(status="pending", requested_by=requested_by, requested_at=_now())
    return _write(jd_path, record)


def approve(jd_path: str | Path, *, version: int, approved_by: str) -> dict | None:
    """HR Head signs off. Returns the updated record, or None if the record is
    for a different version or was never requested."""
    record = load_approval(jd_path)
    if record.get("version") != version or record.get("status") != "pending":
        return None
    record.update(status="approved", approved_by=approved_by, approved_at=_now())
    return _write(jd_path, record)


def status_for(jd_path: str | Path, *, version: int) -> dict:
    """The approval record for `version` — the stored one when it matches, else a
    fresh `none` default (a stale record for an older version doesn't gate a newer
    one; the caller only gates when the latest version actually has flagged rows)."""
    record = load_approval(jd_path)
    return record if record.get("version") == version else _default(version)
