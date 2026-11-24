"""Background preparation of a requisition's résumé batch (2026-08-26).

WHY THIS EXISTS. "Add résumés" used to do ALL of its work inside the upload
request: content-hash de-dup, a pypdf text pass per file for contact identity,
Bulk Print splitting (a second text pass over every page of every packet), and a
re-parse of each written slice. On the hosted single-vCPU plan with the batch on
an Azure Files share, a real 6-packet / 232-candidate upload was 15-30 minutes of
in-request work: the browser showed one spinner the whole time, the platform's
~230s gateway timeout loomed over every request, and the pure-Python pypdf work
starved every other page of the app (handlers share one threadpool and one CPU).

THE SHAPE NOW. Uploading STAGES files (a byte copy into `_incoming/` — costs
bandwidth, parses nothing) and returns immediately. One detached worker process
per batch drains the staging area — de-dup, split, index — updating a state file
the Candidates tab polls, so candidates appear as they become ready and the
recruiter can leave the page: the worker doesn't need the browser. "Screen
résumés" refuses to start while preparation is outstanding, and uploads are
refused while a screening run holds the batch, so the two can never overlap.

WHAT DELIBERATELY DID NOT MOVE. De-duplication (same file / same person) stays
part of ADDING, because it decides what the candidate list shows — deferring it
to screen time would display rows that later silently vanish. Redaction,
extraction and scoring were already screen-time work and are untouched. Precision
is untouched too: the worker applies the same de-dup rules to the same inputs
(slice identity is still read from the written slice — see _apply_split's revert
note), and a file whose examination fails is KEPT (hash-de-dup only), because
"we couldn't read it" must never become "we dropped it".

One intake improvement while restructuring: a multi-résumé packet is now always
de-duped by content hash ONLY. The old single-file path contact-de-duped any
packet with few enough addresses, so a small Bulk Print sharing ONE person with
the batch dropped the whole file — everyone else in it lost.

OWNERSHIP RULES (crash-safe by construction):
  * The worker owns `.prep_state.json` while it holds the `.prep.worker` lock.
  * reconcile() writes state only while briefly holding that same lock itself,
    so a live worker is never raced. The OS releases the lock when a holder
    dies, so "acquirable" doubles as the death signal.
  * A staged file is removed only AFTER being processed and counted; a crash
    leaves it in `_incoming/` and the next reconcile() re-spawns a worker. (A
    crash in the instant between counting and removing re-processes one file as
    a duplicate — the honest direction to be wrong in.)

THE CONTACT INDEX. `.contact_index.json` persists each batch file's content hash
+ contact tokens (email/phone, extracted locally for de-dup only — the same data
contacts.py has always read; stored beside the résumés it describes, in the same
access-controlled folder, deleted with them). Without it, every new worker
process re-extracted text from the WHOLE existing batch before touching new work
— the exact cost this module exists to remove. Entries are validated by
(size, mtime_ns) on every load; a stale entry costs one re-read, never a wrong
identity.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import try_file_lock

log = logging.getLogger(__name__)

INCOMING_DIRNAME = "_incoming"
STATE_NAME = ".prep_state.json"
INDEX_NAME = ".contact_index.json"
LOCK_NAME = ".prep.worker"  # portalocker adds .lock -> .prep.worker.lock
LOG_NAME = ".prep.log"

# A worker heartbeats every _HEARTBEAT_SECONDS; state older than _GRACE_SECONDS
# with an acquirable lock means the worker is dead (or a spawn never booted).
_HEARTBEAT_SECONDS = 10.0
_GRACE_SECONDS = 45.0
# Spawns that never produce progress stop being retried after this many attempts,
# so a broken environment degrades to a visible error instead of a spawn loop.
_MAX_ATTEMPTS = 3

# How long after a session finishes a newly-staged file still counts as the SAME
# upload burst.
#
# A chunked upload arrives as SEVERAL requests seconds apart (api.ts posts ≤8 MB
# per group), and the worker can finish one group before the next lands. Starting a
# fresh session per group made the progress numbers restart mid-upload — a 6-group
# upload counted "1 of 6 … 2 of 6" and then announced "5 of 5" — and the closing
# sentence reported only the final group, badly under-stating what was added. The
# counters belong to the BURST, not to whichever group the worker happened to be
# draining. Well clear of any real gap between groups, and short enough that a
# genuinely separate upload minutes later starts clean.
_BURST_WINDOW_SECONDS = 120.0

# Serializes state writes BETWEEN THE WORKER'S OWN THREADS (drain loop +
# heartbeat). Cross-process safety comes from the .prep.worker lock, not this.
#
# Re-entrant, and taken INSIDE _write_state rather than only by its callers: a
# write is read-file → write temp → os.replace, so two threads racing it made one
# replace the temp the other was about to move, and the loser died with ENOENT on
# `.prep_state.json.tmp`. That is precisely what happened on the first hosted
# upload — _finalize wrote unguarded, the heartbeat replaced its temp, and a
# COMPLETELY SUCCESSFUL preparation reported "hit a problem" to the recruiter.
# Guarding the primitive instead of each caller means a new call site cannot
# reintroduce it.
_STATE_MUTEX = threading.RLock()


def _inline() -> bool:
    """Test/CI escape hatch: run the worker synchronously in-process instead of
    spawning. Same run_worker(), same state machine — only the transport differs,
    so route tests exercise the real preparation code deterministically."""
    return os.environ.get("BATCH_PREP_INLINE", "").strip().lower() in ("1", "true", "yes")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- staging -------------------------------------------------------------------------


def incoming_dir(folder: str | Path) -> Path:
    return Path(folder) / INCOMING_DIRNAME


def incoming_files(folder: str | Path) -> list[Path]:
    staging = incoming_dir(folder)
    if not staging.exists():
        return []
    return sorted(p for p in staging.iterdir()
                  if p.is_file() and not p.name.startswith((".", "~$")))


def stage_bytes(file_name: str, data: bytes, folder: str | Path) -> str:
    """One uploaded file into the staging area -> 'staged' | 'unsupported'.
    Nothing is parsed here — this is the whole synchronous cost of an upload."""
    from resume_screener.ats.local_folder import RESUME_SUFFIXES, _free_name

    if Path(file_name).suffix.lower() not in RESUME_SUFFIXES:
        return "unsupported"
    staging = incoming_dir(folder)
    staging.mkdir(parents=True, exist_ok=True)
    target = _free_name(staging, Path(file_name).name)
    target.write_bytes(data)
    return "staged"


def stage_folder(source: str | Path, folder: str | Path, *,
                 within: str | Path) -> tuple[int, int]:
    """COPY résumé files from a folder (recursive) into staging -> (staged,
    unsupported). Copy, not reference (addendum 05): the project stays
    self-contained and retention has one story. Raises BatchError like the old
    synchronous import did.

    Only from inside `within`, the legacy shared résumés folder this import exists
    for (2026-10-16). It used to copy from any folder the server could read —
    another requisition's batch included, which is a way round who may see whose
    candidates. The scope is checked before anything else, so a path outside it
    learns nothing (not even whether it exists), messages never echo a server path,
    and a file reached through a link that leads outside the folder is skipped."""
    from resume_screener.ats.local_folder import RESUME_SUFFIXES, BatchError

    root = Path(within).resolve()
    given = Path(str(source).strip().strip('"'))
    source = (given if given.is_absolute() else Path.cwd() / given).resolve()
    if source != root and root not in source.parents:
        raise BatchError(
            f"Import only from the shared résumés folder ({Path(within).as_posix()}).")
    folder = Path(folder)
    if not source.exists():
        raise BatchError("That folder doesn't exist.")
    if not source.is_dir():
        raise BatchError("That's a file, not a folder.")
    folder.mkdir(parents=True, exist_ok=True)
    resolved_batch = folder.resolve()
    if source == resolved_batch or resolved_batch in source.parents:
        raise BatchError("That's already this role's batch folder.")
    try:
        entries = sorted(p for p in source.rglob("*") if p.is_file())
    except (OSError, PermissionError) as exc:
        log.warning("Folder import could not read %s: %s", source, exc)
        raise BatchError("Couldn't read that folder — check its permissions.") from exc
    staged = unsupported = 0
    for path in entries:
        if path.name.startswith((".", "~$")):
            continue
        try:
            inside = root in path.resolve().parents
        except OSError:
            inside = False
        if not inside:
            continue  # a link out of the shared folder
        if path.suffix.lower() not in RESUME_SUFFIXES:
            unsupported += 1
            continue
        try:
            outcome = stage_bytes(path.name, path.read_bytes(), folder)
        except (OSError, PermissionError):
            unsupported += 1
            continue
        staged += 1 if outcome == "staged" else 0
    return staged, unsupported


# --- state ---------------------------------------------------------------------------


def _state_path(folder: str | Path) -> Path:
    return Path(folder) / STATE_NAME


def _fresh_session(req_id: str, prior: dict | None = None) -> dict:
    prior = prior or {}
    return {
        "schema": 1, "req_id": req_id, "status": "idle",
        "actor": prior.get("actor", ""), "region": prior.get("region", ""),
        "attempts": 0, "processed": 0, "added": 0, "duplicates": 0,
        "seen_total": 0,  # high-water mark for the progress denominator
        # "Upload and screen": the recruiter's intent, recorded SERVER-side so it
        # survives closing the tab — the whole point is to drop files and walk away.
        "auto_screen": bool(prior.get("auto_screen")),
        "auto_screen_ready": False,   # preparation finished; a run is owed
        "stop_requested": False,      # the chicken-out path (see request_stop)
        "unsupported": 0, "split_files": 0, "split_resumes": 0,
        "errors": [], "current": "", "sentence": "", "message": "",
        "started_at": _iso(), "updated_at": _iso(), "heartbeat": time.time(),
        "pid": None,
    }


def read_state(folder: str | Path) -> dict:
    base = _fresh_session("")
    try:
        raw = json.loads(_state_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if isinstance(raw, dict):
        base.update(raw)
    return base


def _write_state(folder: str | Path, state: dict) -> None:
    with _STATE_MUTEX:
        state["updated_at"] = _iso()
        path = _state_path(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A temp name unique to this writer, so even an unsynchronized writer
        # (another process) can never move the temp this one is about to replace.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)  # never strand a temp on the share
            raise


def _update_state(folder: str | Path, state: dict, **changes) -> None:
    """Worker-side read-modify-write, serialized against the heartbeat thread."""
    with _STATE_MUTEX:
        state.update(changes)
        state["heartbeat"] = time.time()
        _write_state(folder, state)


def _continues_burst(state: dict) -> bool:
    """Is a finished session recent enough that new files belong to the same burst?

    Wall clock (not monotonic) on purpose: the API process and the worker process
    both read this, and only the wall clock is shared between them."""
    raw = state.get("finished_at")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - when).total_seconds()
    return 0 <= age <= _BURST_WINDOW_SECONDS


def _resume_or_restart(state: dict, req_id: str) -> dict:
    """The one session rule, shared by reconcile() and run_worker() so they cannot
    disagree: a crashed session (queued/working/error) keeps its counters, a
    just-finished one continues its BURST, and anything older starts clean."""
    if state.get("status") in ("done", "idle") and not _continues_burst(state):
        return _fresh_session(req_id, prior=state)
    # Continuing: drop the closing summary so a stale sentence can't be shown as
    # though it described the work still arriving.
    state["sentence"] = ""
    state["finished_at"] = ""
    return state


def _stale(state: dict) -> bool:
    """No sign of life for longer than the grace period. Uses the heartbeat the
    worker refreshes every few seconds — updated_at moves with it."""
    return (time.time() - float(state.get("heartbeat") or 0)) > _GRACE_SECONDS


def _live_worker(folder: str | Path, state: dict) -> bool:
    """Is a worker (presumed) alive? Fresh heartbeat while queued/working. The
    lock is the ground truth, but probing it takes a round trip — the heartbeat
    answers the hot path and reconcile() double-checks under the lock."""
    return state.get("status") in ("queued", "working") and not _stale(state)


def session_payload(folder: str | Path) -> dict:
    """The recruiter-facing view of preparation, embedded in GET …/batch.
    `active` = work outstanding (screening must wait); `live` = a worker is
    (presumed) running right now (batch edits must wait)."""
    state = read_state(folder)
    pending = len(incoming_files(folder))
    status = state.get("status", "idle")
    active = pending > 0 or status in ("queued", "working")
    # The denominator is a HIGH-WATER MARK, not processed+pending. A chunked upload
    # adds files while the worker drains them, so the live sum jumps around — a real
    # upload showed "0 of 5", then "1 of 6". It may still CLIMB as later groups land
    # (honest — more files really did arrive), but it never goes backwards, which is
    # what made it read as a reset.
    total = max(int(state.get("seen_total") or 0), state.get("processed", 0) + pending)
    return {
        "active": active,
        "live": _live_worker(folder, state),
        "status": status,
        "processed": state.get("processed", 0),
        "pending": pending,
        "total": total,
        "added": state.get("added", 0),
        "duplicates": state.get("duplicates", 0),
        "unsupported": state.get("unsupported", 0),
        "split_files": state.get("split_files", 0),
        "split_resumes": state.get("split_resumes", 0),
        "failed": len(state.get("errors") or []),
        "current": state.get("current") or None,
        "sentence": state.get("sentence") or None,
        "message": state.get("message") or None,
        # Whether a screening run is owed when this finishes, so the tab can say so
        # instead of the recruiter wondering whether to come back and click.
        "auto_screen": bool(state.get("auto_screen")),
        "stopping": bool(state.get("stop_requested")),
    }


# --- reconcile + spawn -----------------------------------------------------------------


def reconcile(folder: str | Path, req_id: str, *, actor: str = "", region: str = "",
              reset_attempts: bool = False) -> dict:
    """Make reality match intent, idempotently: pending files with no live worker
    -> (re)spawn one; a worker that died after its last file -> finalize its
    bookkeeping. Called from GET …/batch, so ordinary polling self-heals a crash
    — no operator action needed. Returns session_payload().

    Never overlaps screening: uploads are 409'd while a run holds the batch, so
    pending work cannot appear mid-run; and screening refuses to start while
    `active` — the two owners of the folder take strict turns."""
    folder = Path(folder)
    state = read_state(folder)
    pending = incoming_files(folder)

    if not pending:
        # Close the books on a session that has no work left but never finalized:
        # a worker that died between its last processed file and its own finalize
        # (every file was already counted), or an error session with nothing left
        # to retry.
        #
        # That second case is deliberate, not cosmetic. An error whose staged files
        # are gone is not something a recruiter can act on — every file was
        # processed and counted — so leaving a red "hit a problem" card with a
        # Try-again that has nothing to try would be a lie that never clears. A
        # GENUINE failure (a worker that cannot start) still has its files staged,
        # so it keeps its error. Per-file failures survive either way: they are
        # carried in errors[] and reported as "N failed" in the sentence.
        needs_close = (state.get("status") in ("queued", "working") and _stale(state)) \
            or state.get("status") == "error"
        if needs_close:
            lock = try_file_lock(folder / LOCK_NAME)
            if lock is not None:
                try:
                    state = read_state(folder)
                    if (state.get("status") in ("queued", "working", "error")
                            and not incoming_files(folder)):
                        _finalize(folder, state)
                finally:
                    lock.release()
        return session_payload(folder)

    if _live_worker(folder, state) and not reset_attempts:
        return session_payload(folder)  # booting or between heartbeats — give it its grace

    lock = try_file_lock(folder / LOCK_NAME)
    if lock is None:
        return session_payload(folder)  # a worker is alive and holding the folder
    spawn = False
    try:
        state = read_state(folder)
        # queued/working (a dead worker) and error KEEP the session: counters carry
        # across a respawn, and `attempts` keeps climbing so a persistently-crashing
        # worker parks at the cap instead of respawning on every poll forever. A
        # just-finished session continues its burst (see _BURST_WINDOW_SECONDS).
        state = _resume_or_restart(state, req_id)
        if reset_attempts:
            state["attempts"] = 0
            state["message"] = ""
        state["req_id"] = req_id
        if actor:
            state["actor"] = actor
        if region:
            state["region"] = region
        if state.get("attempts", 0) >= _MAX_ATTEMPTS:
            if state.get("status") != "error":
                state["status"] = "error"
                state["message"] = (
                    "Preparing these résumés failed to start after "
                    f"{_MAX_ATTEMPTS} attempts — ask an admin to check {LOG_NAME} "
                    "in this role's batch folder, then use Try again.")
                _write_state(folder, state)
        else:
            state["status"] = "queued"
            state["attempts"] = state.get("attempts", 0) + 1
            state["heartbeat"] = time.time()
            _write_state(folder, state)
            spawn = True
    finally:
        lock.release()
    if spawn:
        _spawn(folder, req_id)
    return session_payload(folder)


def _spawn(folder: Path, req_id: str) -> None:
    if _inline():
        run_worker(req_id, folder)
        return
    # Same invocation shape as jobs.start_screening: the CLI callback re-runs
    # dataroot.bootstrap()/env export, so the child lands in the same DATA_ROOT.
    cmd = [sys.executable, "-u", "-m", "resume_screener.cli", "batch-prep", req_id]
    kwargs: dict = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):  # Windows dev
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:  # Linux container
        kwargs["start_new_session"] = True
    try:
        with open(folder / LOG_NAME, "ab") as log_file:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log_file,
                             stderr=log_file, env=os.environ.copy(), **kwargs)
    except Exception as exc:  # noqa: BLE001 — a failed spawn must surface, not 500 the upload
        log.error("batch_prep: could not spawn the worker for %s: %s", req_id, exc)


def checked(folder: str | Path, req_id: str, *, actor: str = "", region: str = "") -> dict:
    """reconcile() under its public name — what API routes call."""
    return reconcile(folder, req_id, actor=actor, region=region)


def set_auto_screen(folder: str | Path, actor: str = "") -> None:
    """Record "screen it when the files are ready" on the session. Written before the
    worker is reconciled so the intent cannot be lost to a closed tab."""
    state = read_state(folder)
    state["auto_screen"] = True
    state["auto_screen_ready"] = False
    if actor:
        state["actor"] = actor
    _write_state(folder, state)


def request_stop(folder: str | Path, req_id: str = "") -> dict:
    """The chicken-out path: ask preparation to stop.

    Cooperative rather than a kill, so the folder is never left half-written: the
    worker checks between files, discards what is still QUEUED, and keeps every
    candidate it already added. Also clears any "and then screen it" intent — a
    recruiter who stops preparing did not ask for a run."""
    state = read_state(folder)
    state["stop_requested"] = True
    state["auto_screen"] = False
    state["auto_screen_ready"] = False
    _write_state(folder, state)
    _emit("batch_prep_stop_requested", state.get("actor", ""), req_id or state.get("req_id", ""),
          "Stopped preparing résumés")
    return session_payload(folder)


def _stop_requested(folder: str | Path) -> bool:
    return bool(read_state(folder).get("stop_requested"))


def discard_staged(folder: str | Path) -> int:
    """Delete everything still waiting to be prepared; returns how many. The files
    already turned into candidates are untouched — stopping is not undoing."""
    dropped = 0
    for path in incoming_files(folder):
        try:
            path.unlink()
            dropped += 1
        except OSError:
            pass
    return dropped


def clear_prep_files(folder: str | Path) -> None:
    """Remove staging + state + index + log (used by Clear batch, once no worker
    is live). The candidate files themselves are the caller's business."""
    folder = Path(folder)
    staging = incoming_dir(folder)
    if staging.exists():
        for p in list(staging.iterdir()):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            staging.rmdir()
        except OSError:
            pass
    for name in (STATE_NAME, INDEX_NAME, LOG_NAME, LOCK_NAME + ".lock"):
        try:
            (folder / name).unlink()
        except OSError:
            pass
    for stranded in list(folder.glob(f"{STATE_NAME}.*.tmp")) + list(folder.glob(f"{INDEX_NAME}.*.tmp")):
        stranded.unlink(missing_ok=True)


# --- the contact index ------------------------------------------------------------------


def _index_path(folder: str | Path) -> Path:
    return Path(folder) / INDEX_NAME


def _load_index(folder: Path) -> dict:
    try:
        raw = json.loads(_index_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("schema", 1)
    raw.setdefault("files", {})
    raw.setdefault("sources", {})
    return raw


def _save_index(folder: Path, index: dict) -> None:
    path = _index_path(folder)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")  # per-writer, like the state
    try:
        tmp.write_text(json.dumps(index, indent=0), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _entry_valid(folder: Path, name: str, entry: dict) -> bool:
    try:
        stat = (folder / name).stat()
    except OSError:
        return False
    return stat.st_size == entry.get("size") and stat.st_mtime_ns == entry.get("mtime_ns")


def _index_file(index: dict, folder: Path, name: str, digest: str,
                tokens: set[str], multi: bool) -> None:
    try:
        stat = (folder / name).stat()
    except OSError:
        return
    index["files"][name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                            "hash": digest, "tokens": sorted(tokens), "multi": multi}


# --- the worker -------------------------------------------------------------------------


def _emit(action: str, actor: str, req_id: str, summary: str,
          data: dict | None = None) -> None:
    """Best-effort event onto the app spine — telemetry must never sink a prep."""
    try:
        from resume_screener.events import emit_event

        emit_event(action, actor=actor or "system", req_id=req_id or None, summary=summary,
                   data=data or {})
    except Exception:  # noqa: BLE001
        log.debug("batch_prep: event emit failed", exc_info=True)


def _finalize(folder: Path, state: dict) -> None:
    """Close a session: build the recruiter sentence (same vocabulary the old
    synchronous upload answered with) and mark done."""
    from resume_screener.ats.local_folder import ImportReport

    report = ImportReport(added=state.get("added", 0), duplicates=state.get("duplicates", 0),
                          unsupported=state.get("unsupported", 0),
                          errors=list(state.get("errors") or []))
    sentence = report.sentence()
    split_files, split_resumes = state.get("split_files", 0), state.get("split_resumes", 0)
    if split_files:
        sentence += (f" · {split_files} bulk file{'s' if split_files != 1 else ''} "
                     f"split into {split_resumes} résumés")
    stopped = bool(state.get("stop_requested"))
    if stopped:
        sentence = f"Stopped — {sentence} before you stopped"
    # An owed screening run is recorded on the STATE, not fired from here: this runs in
    # the worker process, whose in-memory job store is not the API's, so a run started
    # here could never be tracked or stopped from the UI. The watchdog in the API
    # process picks it up (see autoscreen_due).
    state.update(status="done", sentence=sentence, current="", attempts=0,
                 message="", finished_at=_iso(),
                 auto_screen_ready=bool(state.get("auto_screen")) and not stopped,
                 stop_requested=False)
    _write_state(folder, state)
    _emit("batch_prepared", state.get("actor", ""), state.get("req_id", ""), sentence)


def _heartbeat_loop(folder: Path, state: dict, stop: threading.Event) -> None:
    while not stop.wait(_HEARTBEAT_SECONDS):
        with _STATE_MUTEX:
            state["heartbeat"] = time.time()
            try:
                _write_state(folder, state)
            except OSError:
                pass  # a hiccup on the share; the next beat retries


def _folder_for(req_id: str) -> Path:
    from resume_screener.api import service
    from resume_screener.ats.local_folder import batch_dir

    return batch_dir(service.config().ats.roles_dir, req_id)


def run_worker(req_id: str, folder: Path | None = None) -> int:
    """Drain the staging area for one requisition. Returns a process exit code.
    Safe to start any number of times: the folder lock admits one worker, and a
    second comer exits 0 immediately."""
    folder = Path(folder) if folder is not None else _folder_for(req_id)
    lock = try_file_lock(folder / LOCK_NAME)
    if lock is None:
        return 0  # another worker already owns this batch
    stop = threading.Event()
    state = read_state(folder)
    try:
        state = _resume_or_restart(state, req_id)  # the shared session rule
        state["req_id"] = state.get("req_id") or req_id
        _update_state(folder, state, status="working", pid=os.getpid(), message="")
        heartbeat = threading.Thread(target=_heartbeat_loop, args=(folder, state, stop),
                                     daemon=True)
        heartbeat.start()
        try:
            os.nice(5)  # background work yields to interactive requests (Linux)
        except (AttributeError, OSError):
            pass
        _drain(folder, state)
        _finalize(folder, state)
        return 0
    except Exception as exc:  # noqa: BLE001 — the state file is how failure surfaces
        log.exception("batch_prep worker failed for %s", req_id)
        _update_state(folder, state, status="error",
                      message=f"Preparation stopped: {exc}")
        _emit("batch_prep_failed", state.get("actor", ""), req_id, str(exc))
        return 1
    finally:
        stop.set()
        lock.release()


def _drain(folder: Path, state: dict) -> None:
    from resume_screener import feature_flags

    index = _load_index(folder)
    split_on = feature_flags.flag_enabled("multi_resume_split", state.get("region") or None)
    known_hashes, known_contacts, source_hashes = _warm_sets(folder, index, split_on, state)
    while True:
        pending = incoming_files(folder)
        if not pending:
            break
        seen = max(int(state.get("seen_total") or 0),
                   state.get("processed", 0) + len(pending))
        for staged in pending:
            if _stop_requested(folder):
                # Between files, never mid-file: a partly written candidate would be
                # worse than an unprepared one.
                dropped = discard_staged(folder)
                _update_state(folder, state, current="", stop_requested=True,
                              message=(f"Stopped — {dropped} file(s) not prepared"
                                       if dropped else "Stopped"))
                return
            _update_state(folder, state, current=staged.name, seen_total=seen)
            try:
                _process_one(folder, staged, state, index, split_on,
                             known_hashes, known_contacts, source_hashes)
            except Exception as exc:  # noqa: BLE001 — one bad file must not sink the batch
                log.exception("batch_prep: %s failed", staged.name)
                errors = list(state.get("errors") or [])
                errors.append(f"{staged.name}: {exc}")
                _update_state(folder, state, errors=errors,
                              processed=state.get("processed", 0) + 1)
                try:
                    staged.unlink()  # do not retry-loop a poison file
                except OSError:
                    pass
            _save_index(folder, index)
        # New files may have been staged while we worked — loop until truly dry.
    _update_state(folder, state, current="")


def _warm_sets(folder: Path, index: dict, split_on: bool,
               state: dict) -> tuple[set[str], set[str], set[str]]:
    """(content hashes, single-candidate contact tokens, stored-source hashes)
    for everything ALREADY in the batch — from the persisted index where valid,
    computed (and indexed) where not.

    A file the index doesn't know is ADOPTED with the full intake examination —
    including the bulk-split check, which is what the old per-upload folder
    rescan provided for files that appeared out-of-band (a share copy, a batch
    predating this worker). That keeps the one behavior of the rescan worth
    keeping, while making it once-per-file-ever instead of per-upload."""
    from resume_screener import contacts, resume_split
    from resume_screener.ats import local_folder

    known_hashes: set[str] = set()
    known_contacts: set[str] = set()
    source_hashes: set[str] = set()

    manifest = resume_split.read_manifest(folder)
    derived_names = {name for entry in manifest.values() for name in entry.get("derived", [])}

    for path in local_folder.batch_files(folder):
        entry = index["files"].get(path.name)
        if entry is not None and _entry_valid(folder, path.name, entry):
            known_hashes.add(entry["hash"])
            tokens = set(entry.get("tokens") or [])
            if tokens and not entry.get("multi"):
                known_contacts |= tokens
            # Warm the process-wide cache too, for any path that still asks it.
            contacts.preload_tokens(path, entry["size"], entry["mtime_ns"], tokens)
            continue
        # Unknown to the index — examine once, as intake would.
        try:
            data = path.read_bytes()
        except OSError:
            continue
        is_pdf = path.suffix.lower() == ".pdf"
        if is_pdf:
            # A file placed in the batch by hand is cleaned the way an upload is.
            data = _clean_pdf_in_place(path, data, state)
        digest = local_folder.candidate_id_for(data)
        if is_pdf and split_on and path.name not in derived_names:
            segments, method, expected, full_text = resume_split.examine_pdf(data)
            tokens = contacts.tokens_from_text(full_text)
            if len(segments) > 1:
                result = resume_split._apply_split(
                    folder, path, data, segments, method, _iso(), expected,
                    known_contacts=known_contacts)
                _absorb_split(folder, state, index, result, digest,
                              known_hashes, source_hashes, adopted=True)
                continue
        else:
            tokens = contacts.contact_tokens(data, path.suffix)
        multi = contacts.holds_many_candidates(tokens)
        known_hashes.add(digest)
        if tokens and not multi:
            known_contacts |= tokens
        local_folder._remember_hash(path, digest)
        contacts.remember_tokens(path, tokens)
        _index_file(index, folder, path.name, digest, tokens, multi)

    sources_dir = folder / resume_split.SOURCES_DIRNAME
    if sources_dir.exists():
        for path in sorted(p for p in sources_dir.iterdir() if p.is_file()):
            entry = index["sources"].get(path.name)
            if entry is not None and _entry_valid(sources_dir, path.name, entry):
                source_hashes.add(entry["hash"])
                continue
            try:
                digest = local_folder.candidate_id_for(path.read_bytes())
            except OSError:
                continue
            source_hashes.add(digest)
            try:
                stat = path.stat()
                index["sources"][path.name] = {"size": stat.st_size,
                                               "mtime_ns": stat.st_mtime_ns, "hash": digest}
            except OSError:
                pass
    _save_index(folder, index)
    return known_hashes, known_contacts, source_hashes


def _absorb_split(folder: Path, state: dict, index: dict, result, source_digest: str,
                  known_hashes: set[str], source_hashes: set[str], *,
                  adopted: bool = False) -> None:
    """Fold one _apply_split outcome into the session's sets, counters and index."""
    from resume_screener import resume_split

    for slice_name, meta in result.slice_meta.items():
        known_hashes.add(meta["hash"])
        _index_file(index, folder, slice_name, meta["hash"],
                    set(meta.get("tokens") or []), multi=False)
    source_hashes.add(source_digest)
    # The packet moved into _sources/ under a possibly-suffixed name — index it
    # from the manifest entry _apply_split just wrote.
    entry = resume_split.read_manifest(folder).get(result.source) or {}
    stored_rel = entry.get("source_stored") or ""
    stored = folder / stored_rel if stored_rel else None
    if stored is not None and stored.exists():
        try:
            stat = stored.stat()
            index["sources"][stored.name] = {"size": stat.st_size,
                                             "mtime_ns": stat.st_mtime_ns,
                                             "hash": source_digest}
        except OSError:
            pass
    changes = {
        "split_files": state.get("split_files", 0) + 1,
        "split_resumes": state.get("split_resumes", 0) + result.count,
    }
    if not adopted:
        changes["added"] = state.get("added", 0) + 1
        changes["processed"] = state.get("processed", 0) + 1
    _update_state(folder, state, **changes)


def _clean_pdf_in_place(path: Path, data: bytes, state: dict) -> bytes:
    """A PDF's active content removed (pdfsafe, 2026-10-16, pen-test RS-04) and the
    cleaned bytes written over the file atomically — BEFORE it is hashed, so the
    candidate id is the clean file's and a split slices the clean file. A clean file
    is left exactly as it is. A failed write propagates: the file is then dropped
    with an error line, never stored as it came."""
    from resume_screener import pdfsafe

    clean, removed = pdfsafe.strip(data)
    if not removed:
        return data
    tmp = path.with_name(f".{path.name}.clean.tmp")  # dot-named: no listing sees it
    tmp.write_bytes(clean)
    os.replace(tmp, path)
    log.warning("batch_prep: removed active content from %s (%s)", path.name, ", ".join(removed))
    _emit("pdf_active_content_removed", state.get("actor", ""), state.get("req_id", ""),
          "Removed active content from 1 résumé file",
          {"file": path.name, "removed": removed})
    return clean


def _process_one(folder: Path, staged: Path, state: dict, index: dict, split_on: bool,
                 known_hashes: set[str], known_contacts: set[str],
                 source_hashes: set[str]) -> None:
    """One staged file through the same intake rules the synchronous path applied:
    hash de-dup -> (single files only) contact de-dup -> place in batch -> split
    if it's a multi-résumé packet. Exactly ONE text-extraction pass per file."""
    from resume_screener import contacts, resume_split
    from resume_screener.ats import local_folder

    def bump(**extra) -> None:
        _update_state(folder, state, processed=state.get("processed", 0) + 1, **extra)

    suffix = staged.suffix.lower()
    if suffix not in local_folder.RESUME_SUFFIXES:
        staged.unlink()
        bump(unsupported=state.get("unsupported", 0) + 1)
        return
    data = staged.read_bytes()
    if suffix == ".pdf":
        data = _clean_pdf_in_place(staged, data, state)
    digest = local_folder.candidate_id_for(data)
    if digest in known_hashes or digest in source_hashes:
        staged.unlink()
        bump(duplicates=state.get("duplicates", 0) + 1)
        return

    segments: list = []
    method, expected, tokens = "single", None, set()
    if suffix == ".pdf" and split_on:
        segments, method, expected, full_text = resume_split.examine_pdf(data)
        tokens = contacts.tokens_from_text(full_text)
    else:
        tokens = contacts.contact_tokens(data, suffix)

    if len(segments) > 1:
        # A multi-résumé packet: content-hash de-dup only (its 40+ addresses are
        # not one person's identity), then split into per-candidate files whose
        # own identities de-dup individually inside _apply_split.
        target = local_folder._free_name(folder, staged.name)
        os.replace(staged, target)
        result = resume_split._apply_split(folder, target, data, segments, method,
                                           _iso(), expected, known_contacts=known_contacts)
        _absorb_split(folder, state, index, result, digest, known_hashes, source_hashes)
        return

    multi = contacts.holds_many_candidates(tokens)
    if tokens and not multi:
        if tokens & known_contacts:
            staged.unlink()  # same person (shared email/phone), different file
            bump(duplicates=state.get("duplicates", 0) + 1)
            return
        known_contacts |= tokens
    target = local_folder._free_name(folder, staged.name)
    os.replace(staged, target)
    known_hashes.add(digest)
    local_folder._remember_hash(target, digest)
    contacts.remember_tokens(target, tokens)
    _index_file(index, folder, target.name, digest, tokens, multi)
    bump(added=state.get("added", 0) + 1)


# --- "upload and screen": the owed run ------------------------------------------------
#
# The recruiter's whole ask is "drop the files and go to lunch", so the intent cannot
# live in the browser: a client-side trigger dies with the tab, which is exactly the
# case this exists for. The intent is recorded on the prep state, the worker marks it
# READY when preparation finishes, and a watchdog thread in the API process starts the
# run. The API process is the right home because its job store is what the UI reads —
# a run spawned from the worker process could never be tracked, paused or stopped.


def autoscreen_due(roles_dir: str | Path) -> list[str]:
    """Requisition ids whose preparation finished and which are owed a screening run."""
    root = Path(roles_dir)
    if not root.exists():
        return []
    due: list[str] = []
    for role in sorted(root.iterdir()):
        if not role.is_dir():
            continue
        state = read_state(role / "resumes")
        if state.get("auto_screen_ready") and not incoming_files(role / "resumes"):
            due.append(state.get("req_id") or role.name)
    return due


def clear_autoscreen(folder: str | Path, note: str = "") -> None:
    """Forget an owed run — it started, or it can never start. Cleared either way so a
    requisition that cannot screen (no checklist, say) is not retried forever."""
    state = read_state(folder)
    state["auto_screen"] = False
    state["auto_screen_ready"] = False
    if note:
        state["message"] = note
    _write_state(folder, state)


_WATCHDOG_SECONDS = 15.0
_watchdog_started = False


def start_autoscreen_watchdog() -> None:
    """Start the once-per-process thread that fires owed runs. Idempotent, daemon, and
    defensive: it must never take the API down, so every iteration swallows its own
    errors and the loop keeps going."""
    global _watchdog_started
    if _watchdog_started or _inline():
        return  # tests drive prep synchronously and start runs explicitly
    _watchdog_started = True

    def loop() -> None:
        while True:
            time.sleep(_WATCHDOG_SECONDS)
            try:
                _fire_due()
            except Exception:  # noqa: BLE001 — a watchdog that dies is worse than useless
                log.debug("batch_prep watchdog iteration failed", exc_info=True)

    threading.Thread(target=loop, daemon=True, name="autoscreen-watchdog").start()


def _fire_due() -> None:
    from resume_screener.api import jobs, service
    from resume_screener.ats.local_folder import batch_dir

    cfg = service.config()
    for req_id in autoscreen_due(cfg.ats.roles_dir):
        folder = batch_dir(cfg.ats.roles_dir, req_id)
        req = next((r for r in service.requisitions(cfg) if r.req_id == req_id), None)
        if req is None:
            clear_autoscreen(folder, "The requisition is gone — no screening started.")
            continue
        blocked = _screen_blocked_reason(cfg, req)
        if blocked:
            clear_autoscreen(folder, blocked)
            _emit("batch_autoscreen_skipped", read_state(folder).get("actor", ""), req_id, blocked)
            continue
        actor = read_state(folder).get("actor", "") or "system"
        clear_autoscreen(folder)  # BEFORE starting, so a crash cannot double-fire
        jobs.start_screening(req_id, req.jd_ref, actor=actor)
        _emit("batch_autoscreen_started", actor, req_id,
              "Screening started automatically once the résumés were ready")


def _screen_blocked_reason(cfg, req) -> str:
    """Why this requisition cannot screen right now — the same gates POST /screen
    applies, in plain language. Empty string means it can."""
    from resume_screener.api import jobs, service

    if service.is_closed(req):
        return "This requisition is closed, so screening didn't start."
    if not service.jd_present(req):
        return "No job description, so screening didn't start."
    rubric = service.rubric_for(req)
    if rubric is None:
        return "No approved checklist, so screening didn't start."
    gate = service.checklist_gate(req, rubric)
    if not gate.get("ready"):
        return str(gate.get("reason") or "The checklist isn't approved, so screening didn't start.")
    if service.batch_count(cfg, req) == 0:
        return "No résumés in the batch, so screening didn't start."
    if jobs.screen_lock(req.req_id) is not None:
        return ""  # a run is already going — that IS the outcome the recruiter wanted
    return ""
