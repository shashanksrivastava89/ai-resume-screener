"""The ONE app-event spine (P29, Addendum 11 Part D2): signals/events.jsonl.

Requisition/application events — close/reopen/snooze, screening completions,
history views, and every later v2 domain (suggestions, takes, preferences,
flags) — append here as one JSON line each. The governance audit log
(signals/audit_log.jsonl) stays RESERVED for config publishes and review
sign-offs; req/app events must never enter it, or the "Draft configuration —
pending review" banner would trip on ordinary recruiter activity
(governance.review_pending has a regression test pinning this).

Line shape: {ts (UTC ISO), kind, actor, actor_role, req_id?, candidate_id?,
summary, data{}}. `summary` is plain recruiter language — activity rails and
the audit trail render it verbatim. Writes take the cross-process file lock
(same sidecar mechanics as decisions.jsonl); reads skip corrupt lines so a
half-written line never takes the whole spine down.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from resume_screener import reqcache
from resume_screener.locking import file_lock
from resume_screener.pathkey import canon

EVENTS_LOG = Path("signals/events.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_db() -> bool:
    """True when the event spine lives in the database (EVENTS_BACKEND=db).
    Monkeypatched in tests; the default (jsonl) keeps the zero-infra file spine."""
    from resume_screener.config import load_db_settings

    return load_db_settings().events_backend.strip().lower() == "db"


def emit_event(
    kind: str,
    *,
    actor: str,
    actor_role: str = "",
    req_id: str | None = None,
    candidate_id: str | None = None,
    summary: str = "",
    data: dict | None = None,
    log_path: str | Path = EVENTS_LOG,
) -> dict:
    """Append one event line; returns the entry as written."""
    entry: dict = {"ts": _now(), "kind": kind, "actor": actor, "actor_role": actor_role}
    if req_id is not None:
        entry["req_id"] = req_id
    if candidate_id is not None:
        entry["candidate_id"] = candidate_id
    entry["summary"] = summary
    entry["data"] = data or {}
    # Almost every change to a requisition passes through here, so this is the
    # broadest place to tell the dashboard index a card may have moved. It is one of
    # THREE mechanisms (see dashboard_index), never the only one — a change that
    # emits no event is still caught by the explicit hooks or the validation sweep.
    if req_id:
        from resume_screener import dashboard_index

        dashboard_index.invalidate(req_id)
    if _use_db():
        from resume_screener.db.repositories import events as events_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            events_repo.append(session, entry)
        return entry
    path = Path(log_path)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


@reqcache.memo(key=lambda req_id=None, kinds=None, log_path=EVENTS_LOG:
               (req_id, tuple(kinds) if kinds is not None else None, str(log_path)))
def load_events(
    req_id: str | None = None,
    kinds: Iterable[str] | None = None,
    log_path: str | Path = EVENTS_LOG,
) -> list[dict]:
    """Events oldest-first, optionally filtered to one requisition and/or a set
    of kinds. Corrupt lines are skipped, never fatal.

    Read once per request (reqcache) and parsed once per FILE STATE across
    requests: the spine is append-only and grows for the life of the deployment,
    and the dashboard asked for it 2-3 times per requisition — each a full read and
    JSON parse of every line ever written. The parsed list is cached on the file's
    (size, mtime) exactly like feedback.load_decisions; filters apply to the
    cached list."""
    if _use_db():
        from resume_screener.db.repositories import events as events_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            return events_repo.load(session, req_id, kinds)
    entries = _parsed_events(Path(log_path))
    wanted = set(kinds) if kinds is not None else None
    if req_id is None and wanted is None:
        return list(entries)
    return [e for e in entries
            if (req_id is None or e.get("req_id") == req_id)
            and (wanted is None or e.get("kind") in wanted)]


# For the people directory's scope (2026-10-16): who acted on which requisition.
# The answer does not depend on who is asking, so it is cached across requests — on
# the parsed list's identity for the file spine (which is itself cached on the file's
# size and mtime), and for 60 s per database for the DB spine.
_ACTORS_CACHE: dict[str, tuple[object, dict[str, set[str]]]] = {}
_ACTORS_TTL = 60.0


def actors_by_req(log_path: str | Path = EVENTS_LOG) -> dict[str, set[str]]:
    """req_id -> the lower-cased actors of its events. Do not mutate the result."""
    import time

    if _use_db():
        from resume_screener.config import load_db_settings
        from resume_screener.db.repositories import events as events_repo
        from resume_screener.db.session import session_scope

        key = "db:" + load_db_settings().database_url
        hit = _ACTORS_CACHE.get(key)
        now = time.monotonic()
        if hit is not None and now - float(hit[0]) < _ACTORS_TTL:  # type: ignore[arg-type]
            return hit[1]
        with session_scope() as session:
            out = events_repo.actors_by_req(session)
        _ACTORS_CACHE[key] = (now, out)
        return out
    entries = _parsed_events(Path(log_path))
    key = canon(Path(log_path))
    hit = _ACTORS_CACHE.get(key)
    if hit is not None and hit[0] is entries:
        return hit[1]
    built: dict[str, set[str]] = {}
    for e in entries:
        if e.get("req_id") and e.get("actor"):
            built.setdefault(e["req_id"], set()).add(str(e["actor"]).lower())
    _ACTORS_CACHE[key] = (entries, built)
    return built


# path -> (size, mtime_ns, parsed entries). Append-only log: any write changes
# both, so a stale hit is impossible; a rewrite in place (retention purge) too.
_EVENTS_CACHE: dict[str, tuple[int, int, list[dict]]] = {}


def _parsed_events(path: Path) -> list[dict]:
    try:
        st = path.stat()
    except OSError:
        return []
    key = canon(path)
    hit = _EVENTS_CACHE.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    events: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            events.append(entry)
    _EVENTS_CACHE[key] = (st.st_size, st.st_mtime_ns, events)
    return events
