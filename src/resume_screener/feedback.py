"""Feedback capture (P12, §1): every human decision -> output/feedback/decisions.jsonl.

Why this file matters more than it looks
----------------------------------------
The decisions log is (a) the audit trail proving a HUMAN made every call, and
(b) the raw material for the production learning loop (§12) — which is offline,
human-gated, and needs to know exactly which model/prompts/rubric/registry/
policy produced the suggestion each human saw. Hence:

- append-only (never rewritten, never deduped — corrections are new lines/rows)
- a Decision without a non-empty reason is REJECTED here in code; the UI blocks
  it too, but the guarantee lives where it can't be bypassed
- versions_from_run_meta() copies the reviewed run's version stamp into each
  decision, so a decision is meaningful even after configs move on

Backend switch (Phase 2): the store is selectable via `FEEDBACK_BACKEND`
(config.DbSettings.feedback_backend). Default "jsonl" keeps the file-based log so
the app runs with zero DB infra; "db" routes through the decisions repository to
Postgres. The Decision CONTRACT and the two guarantees above are identical either
way — callers (API, service) never change.
"""

from __future__ import annotations

import json
from pathlib import Path

from resume_screener import reqcache
from resume_screener.locking import file_lock
from resume_screener.models import Decision
from resume_screener.pathkey import canon


def feedback_path(feedback_dir: str | Path = "output/feedback") -> Path:
    return Path(feedback_dir) / "decisions.jsonl"


def _use_db() -> bool:
    """True when decisions should be read/written from the database (FEEDBACK_BACKEND=db)."""
    from resume_screener.config import load_db_settings

    return load_db_settings().feedback_backend.strip().lower() == "db"


def _req_of(decision: Decision) -> str | None:
    """A decision names its requisition on the contract; older rows may not."""
    return getattr(decision, "req_id", None) or getattr(decision, "req_code", None)


def _touch_dashboard(req_id: str | None) -> None:
    if not req_id:
        return
    from resume_screener import dashboard_index

    dashboard_index.invalidate(req_id)


def record_decision(
    decision: Decision, feedback_dir: str | Path = "output/feedback"
) -> Path | None:
    """Append one decision. Raises ValueError when the reason is empty —
    'agree' without a why is not feedback, it's a click.

    Returns the JSONL path (file backend) or None (db backend). `feedback_dir` is
    ignored by the db backend.
    """
    if not decision.reason.strip():
        raise ValueError("A reason is required for every decision (agree, override or re-rank).")
    if _use_db():
        from resume_screener.db.repositories import decisions as decisions_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            decisions_repo.add_decision(session, decision)
        _touch_dashboard(_req_of(decision))
        return None
    path = feedback_path(feedback_dir)
    # Cross-process lock (P26): the API and any CLI session may append concurrently
    # on the hosted single replica — lines must never interleave.
    _touch_dashboard(_req_of(decision))
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(decision.model_dump_json() + "\n")
    return path


# Parsed-decisions cache keyed on the log's (size, mtime_ns) — the dashboard
# reads the full log several times per request and again per requisition, and the
# file is append-only so any write changes its stamp. Safe across requests: a
# concurrent CLI append bumps mtime, invalidating the entry. File backend only.
_DECISIONS_CACHE: dict[str, tuple[int, int, list[Decision]]] = {}

# The same idea for the DB backend: (count, max id) -> the parsed list. Without it
# every request that touched a decision paid a full-table SELECT plus a Pydantic
# validation of every row — a cost that grows with the deployment's entire decision
# history rather than with anything on screen (2026-09-10).
_DB_DECISIONS_CACHE: dict[str, tuple[tuple, list[Decision]]] = {}


def invalidate_decisions() -> None:
    """Drop both caches. The stamps catch an append on their own; this is for a
    writer in THIS process that wants its own next read to be certain."""
    _DECISIONS_CACHE.clear()
    _DB_DECISIONS_CACHE.clear()


def _load_decisions_db() -> list[Decision]:
    """The decision log from the database, parsed once per (count, max id).

    The file backend has always cached the parsed log on the file's (size, mtime);
    this is the same guarantee for the DB backend, where every request that touched a
    decision was reading the whole table and revalidating every row. The log is
    append-only with an autoincrementing key, so the stamp is exact rather than a
    heuristic — and it costs one aggregate query, not a scan."""
    from resume_screener.db.repositories import decisions as decisions_repo
    from resume_screener.db.session import session_scope

    with session_scope() as session:
        stamp = decisions_repo.table_stamp(session)
        hit = _DB_DECISIONS_CACHE.get("db")
        if hit is not None and hit[0] == stamp:
            return hit[1]
        rows = decisions_repo.list_decisions(session)
    _DB_DECISIONS_CACHE["db"] = (stamp, rows)
    return rows


@reqcache.memo(key=lambda feedback_dir="output/feedback": str(feedback_dir))
def load_decisions(feedback_dir: str | Path = "output/feedback") -> list[Decision]:
    """All recorded decisions, oldest first. Corrupt lines are skipped, not fatal —
    a half-written line must never take the whole log down with it.

    Once per request (reqcache): the dashboard asked for this ~7x per requisition,
    and on the DB backend each ask was a full-table SELECT plus a Pydantic
    validation of every row."""
    if _use_db():
        return _load_decisions_db()
    path = feedback_path(feedback_dir)
    if not path.exists():
        return []
    key = canon(path)
    try:
        st = path.stat()
        cached = _DECISIONS_CACHE.get(key)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            return cached[2]
    except OSError:
        st = None
    decisions: list[Decision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            decisions.append(Decision.model_validate(json.loads(line)))
        except Exception:
            continue  # skip corrupt line; everything else still loads
    if st is not None:
        _DECISIONS_CACHE[key] = (st.st_size, st.st_mtime_ns, decisions)
    return decisions


def latest_decision_for(
    decisions: list[Decision], run_id: str, candidate_id: str
) -> Decision | None:
    """The most recent decision for a candidate on a run (later lines win —
    append-only means a re-decision is just a newer line)."""
    match = None
    for decision in decisions:
        if decision.run_id == run_id and decision.candidate_id == candidate_id:
            match = decision
    return match


def latest_decision_for_req(
    decisions: list[Decision], req_id: str, candidate_id: str
) -> Decision | None:
    """The candidate's most recent decision ON THIS REQUISITION, across its runs
    (later lines win).

    Powers decision CARRY-FORWARD (2026-07-20): a re-screen mints a new run_id,
    but a human decision belongs to the candidate on THIS req, not the run — so
    when the current run has no decision for a candidate that still exists, the
    service layer carries the req's own prior decision forward (badged for
    reconfirm when the config it was made under has since moved).

    Scoped to `req_id` (2026-07-21): a résumé shared across requisitions has the
    same candidate_id, so an unscoped match wrongly pulled another req's decision
    into a FRESH req. Rows without a req_id (pre-2026-07-21) never match, so they
    can't leak either. The log stays append-only."""
    if not req_id:
        return None
    match = None
    for decision in decisions:
        if decision.candidate_id == candidate_id and decision.req_id == req_id:
            match = decision
    return match


def versions_from_run_meta(run_meta: dict) -> dict[str, str]:
    """Flatten a run_meta.json 'versions' block (plus the jurisdiction stamp)
    into the flat dict[str, str] the Decision contract carries."""
    versions = run_meta.get("versions", {})
    flat: dict[str, str] = {}
    for key, value in versions.items():
        if isinstance(value, dict):  # prompts / policy_files sub-dicts
            for sub_key, sub_value in value.items():
                flat[f"{key}.{sub_key}"] = str(sub_value)
        else:
            flat[key] = str(value)
    flat["jurisdiction"] = str(run_meta.get("jurisdiction", ""))
    flat["policy_pack"] = str(run_meta.get("policy_pack", ""))
    return flat
