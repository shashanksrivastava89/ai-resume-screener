"""The requisition list, remembered between requests.

WHY THIS EXISTS (2026-09-10). Building one dashboard card reads the role's sidecar,
resolves its JD, lists its batch folder, stats its run output and reads its run_meta —
about seven filesystem round trips, each one a network hop on the Azure Files share.
That is fine for the pilot's 23 requisitions and ruinous at 500: ~3,500 round trips,
something like 18 seconds, on the single worker everyone else is queued behind. The
database half of the same problem was fixed by asking in bulk (service.prefetch_roles);
the share half cannot be, because there is no bulk read of a file share.

So the cards are stored. `signals/dashboard_index.json` holds one row per requisition:
the card as last computed, plus the cheap state it was computed from.

THREE THINGS KEEP IT HONEST, in order of how much they are relied on:

1. Write-through. Every writer IN THIS PROCESS that can change a card drops that row
   (`invalidate`), and the next load recomputes it. Dropping rather than recomputing
   is deliberate: a write path must not do a card's worth of I/O, and must not fail
   because a card failed to build. This is a latency shortcut, not the guarantee —
   the batch-prep worker and the CLI write from OTHER processes, where an in-process
   hook can say nothing.

2. A validation budget. Each load re-checks up to `budget` rows against their live
   state — the least recently checked first — and recomputes any that moved. This is
   the actual correctness mechanism, because it is the only one that sees a write
   made by another process; it bounds the work per load
   instead of letting it grow with the list. The budget is deliberately larger than
   the pilot's requisition count, so at today's scale EVERY row is verified on every
   load and the index changes nothing observable; it starts saving work only as the
   list outgrows it.

3. Live fields are never stored. Anything that moves second by second — the screening
   lock, which advances while a run is in flight — is overlaid from the bulk read on
   every load, so no run's progress can be served from a remembered card.

A publish of governed configuration moves every card's staleness at once. Dropping
the whole index would be correct and unusable: rebuilding 500 cards takes about two
minutes, so a scoring-model publish would hand the next person a dead dashboard.
Staleness is the ONLY part a publish can move, and it is computed from stamps the run
recorded — so each row keeps those stamps, and a publish recomputes just that field
from them, with no filesystem work at all.

The index is a CACHE. It is additive (an older build ignores the file), deleting it
is always safe, and every failure path falls back to computing the cards directly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from resume_screener.locking import file_lock
from resume_screener.pathkey import canon

if TYPE_CHECKING:  # pragma: no cover - typing only
    from resume_screener.config import AppConfig
    from resume_screener.models import Requisition

log = logging.getLogger("resume_screener.dashboard_index")

INDEX_FILE = Path("signals/dashboard_index.json")
SCHEMA = 1

# Bigger than the pilot's requisition count on purpose — see (2) above.
DEFAULT_BUDGET = 40

# resolved path -> (size, mtime_ns, parsed). Keyed on the RESOLVED path: the file
# name is relative and DATA_ROOT/tests chdir between roots.
_CACHE: dict[str, tuple[int, int, dict]] = {}

# req_ids whose rows this process has invalidated but not yet written back.
_PENDING_DROPS: set[str] = set()


def _key() -> str:
    try:
        return canon(INDEX_FILE)
    except OSError:
        return str(INDEX_FILE)


def _empty() -> dict:
    return {"schema": SCHEMA, "config_stamp": "", "rows": {}}


def _read() -> dict:
    """The stored index, parsed once per (size, mtime)."""
    key = _key()
    try:
        st = INDEX_FILE.stat()
    except OSError:
        _CACHE.pop(key, None)
        return _empty()
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return _empty()
    data.setdefault("rows", {})
    data.setdefault("config_stamp", "")
    _CACHE[key] = (st.st_size, st.st_mtime_ns, data)
    return data


def _write(data: dict) -> None:
    """Atomic replace under the cross-process lock, like every other shared file."""
    try:
        INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(INDEX_FILE):
            tmp = INDEX_FILE.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(INDEX_FILE)
    except OSError:
        pass  # a cache that cannot be written is still a correct app
    _CACHE.pop(_key(), None)


def invalidate(req_id: str | None = None) -> None:
    """Forget one requisition's card (or all of them). Cheap: it never builds a card,
    so a write path pays a set insertion, and the next dashboard load recomputes."""
    if req_id is None:
        _PENDING_DROPS.add("*")
    else:
        _PENDING_DROPS.add(str(req_id))
    _CACHE.clear()


# --- the state a card was computed from -------------------------------------------


def _stat_pair(path: Path) -> list | None:
    """A LIST, not a tuple: the state is compared against a value that has been
    through JSON, which has no tuples. Returning a tuple made every stored row look
    changed, so the index rebuilt everything and saved nothing."""
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _dir_mtime(path: Path) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _stale_inputs(req: "Requisition") -> dict:
    """What re-deciding a card's staleness needs, and nothing else.

    Both reads are already memoised for this request by the card build that is about
    to happen, so capturing them here is free — and it is what lets a governed publish
    refresh 500 cards without touching the share."""
    from resume_screener.api import service

    meta = service.read_run_meta(req.req_id) or {}
    return {"versions": meta.get("versions") or {},
            "has_decisions": bool(service.role_decisions(req.req_id))}


def _refresh_staleness(cfg: "AppConfig", req: "Requisition", row: dict) -> dict:
    """Re-decide one card's staleness from the stamps its run recorded.

    `config_drift` reads the run's `versions` block, the current published stamps
    (cached) and the role's rubric (prefetched in bulk) — no per-requisition file
    read. A row from before this field existed has nothing to work from, so it is
    rebuilt the honest way."""
    from resume_screener.api import service

    inputs = row.get("stale_inputs")
    if not isinstance(inputs, dict):
        return {}
    meta = {"versions": inputs.get("versions") or {}}
    if not meta["versions"]:
        return {**row, "card": {**row["card"], "staleness": None}}
    stale = service.role_staleness(cfg, req, meta,
                                  has_decisions=bool(inputs.get("has_decisions")))
    return {**row, "card": {**row["card"], "staleness": stale}}


def _row_state(cfg: "AppConfig", req: "Requisition") -> list:
    """The cheap identity of everything a card is built from: four stats.

    Not a hash of the card — that would mean building the card, which is the cost
    being avoided. A change to any of these is what makes a card wrong."""
    from resume_screener.api import service
    from resume_screener.ats.local_folder import req_meta_path

    out = service.role_output_dir(req.req_id)
    return [
        _stat_pair(Path(req_meta_path(req.jd_ref))) if req.jd_ref else None,
        _dir_mtime(service.batch_folder(cfg, req)),
        _stat_pair(out / "run_meta.json"),
        _dir_mtime(out / "candidates"),
    ]


def _config_stamp(cfg: "AppConfig") -> str:
    """What every card's staleness is measured against.

    The same stamps a run records and `config_drift` compares against, so a publish
    that could move any card's staleness moves this string — and the index is dropped
    wholesale rather than row by row, which is what a global change deserves."""
    from resume_screener.api import service

    try:
        stamps = service.current_config_stamps(cfg)
    except Exception:  # noqa: BLE001 — an unreadable stamp means "assume it moved"
        return f"unknown-{time.time()}"
    return json.dumps(stamps, sort_keys=True, default=str)


# --- reading ----------------------------------------------------------------------


def cards(cfg: "AppConfig", reqs: list["Requisition"],
          build: Callable[["AppConfig", "Requisition"], dict],
          *, budget: int = DEFAULT_BUDGET) -> list[dict]:
    """One card per requisition, remembered where it is still valid.

    `build` is the real card builder (service.role_summary_safe) — passed in rather
    than imported so this module never becomes a second source of truth for what a
    card is."""
    try:
        return _cards(cfg, reqs, build, budget)
    except Exception:  # noqa: BLE001 — a cache must never be able to break the page
        # ...but it must not fail SILENTLY either: without this the index could stop
        # working entirely and the only symptom would be the slowness it was built to
        # remove.
        log.warning("dashboard index unusable, building cards directly", exc_info=True)
        return [build(cfg, r) for r in reqs]


def _cards(cfg: "AppConfig", reqs: list["Requisition"],
           build: Callable[["AppConfig", "Requisition"], dict], budget: int) -> list[dict]:
    data = _read()
    rows: dict[str, dict] = dict(data.get("rows") or {})
    dirty = False

    stamp = _config_stamp(cfg)
    stale_changed = data.get("config_stamp") != stamp
    if stale_changed:
        dirty = True

    drops = _PENDING_DROPS
    if drops:
        if "*" in drops:
            rows, dirty = {}, True
        else:
            for req_id in drops:
                if rows.pop(req_id, None) is not None:
                    dirty = True
        drops.clear()

    # Round-robin: the rows checked longest ago go first.
    known = [r.req_id for r in reqs if r.req_id in rows]
    known.sort(key=lambda rid: rows[rid].get("checked", 0))
    to_check = set(known[:max(0, budget)])

    now = time.time()
    out: list[dict] = []
    by_id = {r.req_id: r for r in reqs}
    if stale_changed:
        # A publish moves staleness and nothing else, so re-decide that one field in
        # place. Rebuilding instead would be ~2 minutes of share reads at 500 roles.
        for req_id, row in list(rows.items()):
            req = by_id.get(req_id)
            refreshed = _refresh_staleness(cfg, req, row) if req is not None else {}
            if refreshed:
                rows[req_id] = refreshed
            else:
                rows.pop(req_id)        # nothing to re-decide from: rebuild it
    for req in reqs:
        row = rows.get(req.req_id)
        if row is not None and req.req_id in to_check:
            if row.get("state") != _row_state(cfg, req):
                row = None              # the ground moved under this card
            else:
                row["checked"] = now
                dirty = True
        if row is None:
            card = build(cfg, req)
            rows[req.req_id] = {"card": card, "state": _row_state(cfg, req),
                                "stale_inputs": _stale_inputs(req), "checked": now}
            dirty = True
        else:
            card = row["card"]
        out.append(card)

    # Requisitions that no longer exist should not keep their rows forever.
    live = {r.req_id for r in reqs}
    for gone in [rid for rid in rows if rid not in live]:
        rows.pop(gone)
        dirty = True

    if dirty:
        _write({"schema": SCHEMA, "config_stamp": stamp, "rows": rows})
    return _with_live_fields(out)


def _with_live_fields(cards_: list[dict]) -> list[dict]:
    """Overlay what must never come from a remembered card: a run in flight advances
    every few seconds, and the bulk read that answers it costs two reads for the whole
    list either way."""
    from resume_screener.api import service

    try:
        locks = service._screen_locks()  # noqa: SLF001 — same package, one read
    except Exception:  # noqa: BLE001
        return cards_
    out = []
    for card in cards_:
        lock = locks.get(card.get("req_id"))
        brief = None if lock is None else {
            "job_id": lock.get("job_id"), "status": lock.get("status"),
            "started_by": lock.get("actor", ""),
            "done": lock.get("done", 0), "total": lock.get("total", 0),
        }
        if card.get("screening") != brief:
            card = {**card, "screening": brief}
        out.append(card)
    return out


def stored_rows() -> dict[str, Any]:
    """For tests and the Data page: what the index currently holds."""
    return dict(_read().get("rows") or {})
