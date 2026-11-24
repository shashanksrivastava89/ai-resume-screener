"""Hiring-manager takes (P41): input, NEVER decisions.

`output/feedback/hm_takes.jsonl` records a hiring manager's read on candidates —
strong_yes / yes / no with an optional note. A take is the HM's INPUT to the
recruiter; it is deliberately NOT a Decision and never touches decisions.jsonl
(the human decision log stays recruiter-owned). One JSON line per take; a re-take
SUPERSEDES the previous one per (run_id, candidate_id, hm_email) — latest wins.

Line shape: {take_id, req_id, run_id, candidate_id, take, note?, hm_email,
hm_name?, ts}. file_lock on writes; corrupt lines skipped on read.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock

HM_TAKES_LOG = Path("output/feedback/hm_takes.jsonl")
TAKE_VALUES = ("strong_yes", "yes", "no")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(rec: dict) -> tuple:
    return (rec.get("run_id"), rec.get("candidate_id"), rec.get("hm_email"))


def _use_db() -> bool:
    """True when takes live in the database (HM_TAKES_BACKEND=db). Monkeypatched in
    tests; the default (jsonl) keeps the zero-infra file store."""
    from resume_screener.config import load_db_settings

    return load_db_settings().hm_takes_backend.strip().lower() == "db"


def record_takes(
    req_id: str, run_id: str, takes: list[dict], *, hm_email: str, hm_name: str = "",
    log_path: str | Path = HM_TAKES_LOG,
) -> list[dict]:
    """Append one line per take (single or bulk). Returns the written records.
    Invalid take values raise ValueError before anything is written (all-or-nothing)."""
    path = Path(log_path)
    prepared: list[dict] = []
    for t in takes:
        value = str(t.get("take", "")).strip()
        cid = str(t.get("candidate_id", "")).strip()
        if value not in TAKE_VALUES or not cid:
            raise ValueError(f"Invalid take for candidate '{cid}': {value!r}.")
        prepared.append({
            "take_id": uuid.uuid4().hex[:12],
            "req_id": req_id,
            "run_id": run_id,
            "candidate_id": cid,
            "take": value,
            "note": str(t.get("note", "") or "").strip(),
            "hm_email": hm_email,
            "hm_name": hm_name,
            "ts": _now(),
        })
    if _use_db():
        from resume_screener.db.repositories import hm_takes as hm_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            for record in prepared:
                hm_repo.upsert_take(session, record)
        return prepared
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        for record in prepared:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return prepared


def _fold(log_path: str | Path = HM_TAKES_LOG) -> dict[tuple, dict]:
    if _use_db():
        from resume_screener.db.repositories import hm_takes as hm_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            return {_key(r): r for r in hm_repo.list_takes(session)}
    path = Path(log_path)
    if not path.exists():
        return {}
    latest: dict[tuple, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("take_id"):
            latest[_key(rec)] = rec  # re-take supersedes per (run, candidate, hm)
    return latest


def list_takes(
    req_id: str | None = None, run_id: str | None = None,
    log_path: str | Path = HM_TAKES_LOG,
) -> list[dict]:
    """Folded takes (latest per run/candidate/hm), optionally filtered."""
    out = []
    for rec in _fold(log_path).values():
        if req_id is not None and rec.get("req_id") != req_id:
            continue
        if run_id is not None and rec.get("run_id") != run_id:
            continue
        out.append(rec)
    return out


def takes_by_candidate(
    req_id: str, run_id: str, log_path: str | Path = HM_TAKES_LOG,
) -> dict[str, dict]:
    """candidate_id -> latest take for this run (the assigned HM's read). When
    more than one HM ever took, the most recent take per candidate wins."""
    by_cand: dict[str, dict] = {}
    for rec in sorted(list_takes(req_id, run_id, log_path), key=lambda r: r.get("ts", "")):
        by_cand[rec["candidate_id"]] = rec
    return by_cand
