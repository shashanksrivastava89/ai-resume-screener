"""Hiring-manager checklist suggestions (P41): an event-sourced store per requisition.

`jds/<stem>.suggestions.jsonl` sits beside the rubric it proposes edits to (so it
survives candidate-data deletion). One JSON line per transition; the LATEST line
per `suggestion_id` wins. A suggestion is INPUT only — the recruiter/HR still
publishes the checklist; accepting a suggestion just hands its text to the editor.

Line shape: {suggestion_id, req_id, requirement_id, old_text, new_text, note?,
old_kind?, new_kind?, old_importance?, new_importance?,
status: open|accepted|declined|withdrawn, decided_by?, decided_note?,
actor (hm email), actor_name?, ts}. file_lock on writes; corrupt lines skipped
on read. 2026-07-19: a suggestion can now carry a KIND (nice-to-have / must-have
/ hard-gate) and an IMPORTANCE (1–5) change alongside the wording, and the HM
can WITHDRAW their own open suggestion before the recruiter decides.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock

SUFFIX = ".suggestions.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def suggestions_path(jd_path: str | Path) -> Path:
    jd = Path(jd_path)
    return jd.with_name(jd.stem + SUFFIX)


def _append(path: Path, record: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _fold(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("suggestion_id"):
            latest[rec["suggestion_id"]] = rec
    return latest


def add_suggestion(
    jd_path: str | Path, *, req_id: str, requirement_id: str, old_text: str,
    new_text: str, note: str = "", actor: str, actor_name: str = "",
    old_kind: str | None = None, new_kind: str | None = None,
    old_importance: int | None = None, new_importance: int | None = None,
) -> dict:
    """Create an OPEN suggestion (the HM proposes an edit to one requirement) —
    wording, and optionally the kind and/or the importance."""
    record = {
        "suggestion_id": uuid.uuid4().hex[:12],
        "req_id": req_id,
        "requirement_id": requirement_id,
        "old_text": old_text,
        "new_text": new_text,
        "note": note,
        "old_kind": old_kind,
        "new_kind": new_kind,
        "old_importance": old_importance,
        "new_importance": new_importance,
        "status": "open",
        "decided_by": None,
        "decided_note": None,
        "actor": actor,
        "actor_name": actor_name,
        "ts": _now(),
    }
    return _append(suggestions_path(jd_path), record)


def list_suggestions(jd_path: str | Path, *, status: str | None = None) -> list[dict]:
    """Folded suggestions for one requisition, newest-first, optionally by status."""
    out = [rec for rec in _fold(suggestions_path(jd_path)).values()
           if status is None or rec.get("status") == status]
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out


def get_suggestion(jd_path: str | Path, suggestion_id: str) -> dict | None:
    return _fold(suggestions_path(jd_path)).get(suggestion_id)


def locate(jds_dir: str | Path, suggestion_id: str) -> tuple[Path, dict] | None:
    """Find the (jd .md path, folded record) for a suggestion id by scanning the
    per-requisition files — the accept/decline routes carry only the id."""
    for path in sorted(Path(jds_dir).glob(f"*{SUFFIX}")):
        latest = _fold(path)
        if suggestion_id in latest:
            jd_md = path.with_name(path.name[: -len(SUFFIX)] + ".md")
            return jd_md, latest[suggestion_id]
    return None


def decide(
    jd_path: str | Path, suggestion_id: str, *, status: str, decided_by: str,
    decided_note: str = "", edited_text: str | None = None,
) -> dict | None:
    """Record an accept/decline transition (a new line, latest wins). On accept an
    optional `edited_text` overrides new_text (the TA tweaked it before merging)."""
    base = get_suggestion(jd_path, suggestion_id)
    if base is None or base.get("status") != "open":
        return None
    record = {**base, "status": status, "decided_by": decided_by,
              "decided_note": decided_note, "ts": _now()}
    if status == "accepted" and edited_text:
        record["new_text"] = edited_text
    return _append(suggestions_path(jd_path), record)


def withdraw(jd_path: str | Path, suggestion_id: str, *, actor: str) -> dict | None:
    """The HM pulls back their OWN open suggestion before anyone decides
    (2026-07-19). A new line, latest wins — the history stays auditable."""
    base = get_suggestion(jd_path, suggestion_id)
    if base is None or base.get("status") != "open":
        return None
    if str(base.get("actor", "")).lower() != actor.lower():
        return None  # only the author withdraws
    record = {**base, "status": "withdrawn", "ts": _now()}
    return _append(suggestions_path(jd_path), record)
