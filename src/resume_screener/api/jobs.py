"""Screening job runner.

Screening is the compliance-critical path, so instead of re-implementing the
pipeline the API shells out to the exact, tested `screener run` command and
tracks progress. In production (§12) this becomes a real queue + workers behind
the same job interface; the React app only ever sees {status, done, total}.

Where job state lives is a config switch (`jobs_backend`), exactly like the other
migrated seams:
- "memory" (default): an in-process dict — zero infra, single-process/single-user.
  A restart loses in-flight state, but the run itself is cache-resumable, so
  re-screening simply resumes from the content-hash cache.
- "db": the `screening_job` table (via the jobs repository) — state survives a
  restart and is readable by any process, so the subprocess model can grow into a
  real queue + workers without the UI changing.

The subprocess model is identical either way; only the STORE changes. The runner
keeps its live working state (the progress tail) in the thread and pushes a public
snapshot to the store on each meaningful change — so both backends see the same
{status, done, total, message, error}.

Live extras — the subprocess handle (for Stop), the actor, the ETA and the
per-candidate progress names — are inherently in-process (the process that
spawned the child is the only one that can signal it), so they live in module
dicts and are merged into the public payload at read time. The stores and their
pinned shapes stay untouched; a DB-backed multi-worker future replaces this seam
with a real queue, not more columns.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.api.service import role_output_dir

# Match ONLY tqdm's candidate progress bar ("… | 7/12 [00:03<00:05, …]"), which
# always carries the trailing "[elapsed<remaining]" bracket. Other stdout lines
# with a bare "N/M" — notably "11/12 signals enabled" printed at run start — must
# NOT be read as screening progress, or the bar briefly shows a bogus count
# before the real run begins.
_PROGRESS = re.compile(r"(\d+)/(\d+)\s*\[")

# tqdm's bracket is "[elapsed<remaining, rate]" — the remaining part is MM:SS or
# H:MM:SS, or "?" while tqdm has no estimate yet (which simply doesn't match).
_ETA = re.compile(r"\[[\d:]+<((?:\d+:)?\d{1,2}:\d{2})\s*[,\]]")

# The run command prints one machine-readable marker per finished candidate
# ("[candidate-done] <file>" / "[candidate-error] <file>") so the Candidates tab
# can mark rows "screened" vs "screening…" while the run is live. tqdm's bar has
# NO trailing newline, so a marker written right after a bar refresh arrives
# glued onto it ("…12/66 [00:12<01:07][candidate-done] Anita Rao.pdf"). Match
# markers ANYWHERE in the line, and allow several glued together — the name runs
# to the next marker, a "[" (tqdm bracket), or end of line.
_CANDIDATE = re.compile(r"\[candidate-(?:done|error)\]\s+([^\[\r\n]+)")


def _eta_seconds(line: str) -> int | None:
    """Remaining time in seconds from a tqdm progress line, or None when tqdm
    doesn't know yet ("?") / the line carries no estimate."""
    m = _ETA.search(line)
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _use_db() -> bool:
    """True when job state lives in the database (jobs_backend=db)."""
    from resume_screener.config import load_db_settings

    return load_db_settings().jobs_backend.strip().lower() == "db"


# --- stores: where job state lives -------------------------------------------------
#
# Both expose the same tiny surface (create/save/read/read_active) over the public
# job shape {job_id, req_id, status, done, total, message, error}, so the runner and
# the routes are backend-agnostic.


def _public(job_id: str, req_id: str, state: dict) -> dict:
    return {
        "job_id": job_id, "req_id": req_id,
        "status": state.get("status", "running"),
        "done": state.get("done", 0), "total": state.get("total", 0),
        "message": state.get("message", "Starting…"), "error": state.get("error", ""),
    }


class _MemoryStore:
    """In-process job state (the PoC default). Lost on restart; the run resumes from
    cache. A lock keeps the runner's writes and the routes' reads consistent."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, job_id: str, req_id: str, *, region_id: str | None = None) -> None:
        with self._lock:
            self._jobs[job_id] = _public(job_id, req_id, {})

    def save(self, job_id: str, req_id: str, state: dict) -> None:
        with self._lock:
            self._jobs[job_id] = _public(job_id, req_id, state)

    def read(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def read_active(self, req_id: str) -> dict | None:
        with self._lock:
            job = next((j for j in self._jobs.values()
                        if j["req_id"] == req_id and j["status"] == "running"), None)
            return dict(job) if job else None

    def read_all_active(self) -> list[dict]:
        with self._lock:
            return [dict(j) for j in self._jobs.values() if j["status"] == "running"]

    def read_latest(self, req_id: str) -> dict | None:
        with self._lock:
            match = None
            for job in self._jobs.values():  # dict preserves insertion order
                if job["req_id"] == req_id:
                    match = job
            return dict(match) if match else None

    def read_latest_all(self) -> dict[str, dict]:
        with self._lock:
            out: dict[str, dict] = {}
            for job in self._jobs.values():   # insertion order: the last one wins
                out[job["req_id"]] = dict(job)
            return out

    def forget(self, req_id: str) -> int:
        """Drop every record for a requisition (it was deleted). Without this the
        dict outlives the requisition and a recreated one with the same id inherits
        a phantom running/paused job."""
        with self._lock:
            stale = [jid for jid, job in self._jobs.items() if job["req_id"] == req_id]
            for jid in stale:
                del self._jobs[jid]
            return len(stale)


class _DbStore:
    """Job state in the screening_job table (survives restart, worker-readable).
    Writes are best-effort: a DB hiccup must never crash the screening thread or
    fail a run whose real work (the subprocess) is proceeding."""

    def create(self, job_id: str, req_id: str, *, region_id: str | None = None) -> None:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            jobs_repo.create_job(session, job_id, req_id, region_id=region_id)

    def save(self, job_id: str, req_id: str, state: dict) -> None:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        try:
            with session_scope() as session:
                jobs_repo.save_state(session, job_id, state)
        except Exception:  # noqa: BLE001 — never let a state write break screening
            pass

    def read(self, job_id: str) -> dict | None:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            row = jobs_repo.get_job(session, job_id)
            return jobs_repo.job_public(row) if row else None

    def read_active(self, req_id: str) -> dict | None:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            row = jobs_repo.active_job_for(session, req_id)
            return jobs_repo.job_public(row) if row else None

    def read_all_active(self) -> list[dict]:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            return [jobs_repo.job_public(row) for row in jobs_repo.all_active(session)]

    def read_latest_all(self) -> dict[str, dict]:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            return {code: jobs_repo.job_public(row)
                    for code, row in jobs_repo.latest_per_req(session).items()}

    def read_latest(self, req_id: str) -> dict | None:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

    def forget(self, req_id: str) -> int:
        from resume_screener.db.repositories import jobs as jobs_repo
        from resume_screener.db.session import session_scope

        try:
            with session_scope() as session:
                return jobs_repo.delete_for_req(session, req_id)
        except Exception:  # noqa: BLE001 — deletion must not fail on a DB hiccup
            return 0


        with session_scope() as session:
            row = jobs_repo.latest_job_for(session, req_id)
            return jobs_repo.job_public(row) if row else None


_MEMORY = _MemoryStore()  # module singleton: memory state must persist across calls
_DB = _DbStore()          # stateless — safe to share


def _store():
    return _DB if _use_db() else _MEMORY


# --- live in-process state (Stop + per-candidate progress) --------------------------
#
# The subprocess handle and the live extras exist only in the process that spawned
# the run. One lock guards all three dicts; readers merge the extras into the store
# payload so both backends serve identical shapes.

_LIVE_LOCK = threading.Lock()
_PROCS: dict[str, subprocess.Popen] = {}  # job_id -> the running subprocess
_CANCELS: dict[str, tuple[str, str]] = {}  # job_id -> (who asked, "pause"|"cancel")
_LIVE: dict[str, dict] = {}               # job_id -> {actor, started_utc, planned, completed, eta_seconds}


def _reset_live_state() -> None:
    """Test hook: clear the in-process registries (mirrors swapping _MEMORY)."""
    with _LIVE_LOCK:
        _PROCS.clear()
        _CANCELS.clear()
        _LIVE.clear()


def _live_update(job_id: str, **changes) -> None:
    with _LIVE_LOCK:
        _LIVE.setdefault(job_id, {}).update(changes)


def _merge_live(job: dict | None, *, names: bool = True) -> dict | None:
    """Overlay the in-process extras onto a store payload. `names=False` keeps the
    dashboard-wide read light (no 500-name lists on every 2s poll)."""
    if job is None:
        return None
    with _LIVE_LOCK:
        live = dict(_LIVE.get(job["job_id"], {}))
    job["eta_seconds"] = live.get("eta_seconds")
    job["actor"] = live.get("actor", "")
    job["started_utc"] = live.get("started_utc", "")
    if names:
        job["planned"] = list(live.get("planned", []))
        job["completed"] = list(live.get("completed", []))
    return job


def _pop_cancel(job_id: str) -> tuple[str, str]:
    with _LIVE_LOCK:
        return _CANCELS.pop(job_id, ("", ""))


def _resolve_region_id(req_id: str, jurisdiction: str | None) -> str | None:
    """The job's GOVERNING region row id, from its effective jurisdiction (explicit
    override, else the role's stored job location). None when regions aren't seeded
    yet (Phase 7) or the DB is unreachable — the row just stays global."""
    from resume_screener.api import service

    code = jurisdiction
    if not code:
        req = service.find_requisition(req_id)
        code = req.job_location if req else None
    if not code:
        return None
    try:
        from resume_screener.db.repositories import regions as regions_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            return regions_repo.region_id_for_jurisdiction(session, code)
    except Exception:  # noqa: BLE001
        return None


def _batch_names(req_id: str) -> list[str]:
    """The batch's résumé filenames at run start — the run's candidate set, so the
    UI can tell 'screening…' apart from 'added after this run started'."""
    try:
        from resume_screener.api import service
        from resume_screener.ats.local_folder import batch_files

        req = service.find_requisition(req_id)
        if req is None:
            return []
        return [p.name for p in batch_files(service.batch_folder(service.config(), req))]
    except Exception:  # noqa: BLE001 — advisory only, never blocks a run
        return []


def get_job(job_id: str) -> dict | None:
    return _merge_live(_store().read(job_id))


def active_job_for(req_id: str) -> dict | None:
    return _merge_live(_store().read_active(req_id))


def latest_job_for(req_id: str) -> dict | None:
    """The most recent job for a req regardless of status (with its live extras).
    Lets the Candidates tab re-show a just-stopped / failed run after a reload or
    a user switch, when the running-only active_job_for returns nothing."""
    return _merge_live(_store().read_latest(req_id))


def active_jobs() -> list[dict]:
    """Every in-flight screening job (all requisitions). Backs the dashboard's
    live progress column — one read for the whole list."""
    return [_merge_live(j, names=False) for j in _store().read_all_active()]


def screen_locks() -> dict[str, dict]:
    """{req_id: the job locking its batch} for EVERY requisition, in two reads.

    Exactly `screen_lock`'s rule, applied in bulk: a running job locks the batch,
    and so does a paused one when it is the newest job for that role. The dashboard
    asked role by role, which is a query per row (2026-09-10)."""
    store = _store()
    locks = {j["req_id"]: _merge_live(j) for j in store.read_all_active()
             if j.get("req_id")}
    for req_id, job in store.read_latest_all().items():
        if req_id not in locks and job.get("status") == "paused":
            locks[req_id] = _merge_live(job)
    return locks


def screen_lock(req_id: str) -> dict | None:
    """The job LOCKING this req's batch (2026-07-19), or None. A live run locks
    it; so does a PAUSED run (paused = intent to resume, so the batch must not
    change underneath the cached work). Cancelling releases the lock. Only the
    job's initiator (its `actor`) or a Super Admin may pause/cancel/resume —
    enforced at the routes; this helper just says who holds the lock."""
    job = active_job_for(req_id)
    if job is not None:
        return job
    latest = latest_job_for(req_id)
    if latest is not None and latest.get("status") == "paused":
        return latest
    return None


def start_screening(req_id: str, jd_ref: str, *, jurisdiction: str | None = None,
                    limit: int | None = None, concurrency: int | None = None,
                    actor: str = "") -> dict:
    """Kick off `screener run` in a background thread and return the tracking job.
    `actor` (the requesting user's SSO email) is stamped onto the completion event."""
    store = _store()
    existing = store.read_active(req_id)
    if existing:
        return _merge_live(existing)

    job_id = uuid.uuid4().hex[:12]
    region_id = _resolve_region_id(req_id, jurisdiction) if _use_db() else None
    store.create(job_id, req_id, region_id=region_id)
    _live_update(job_id,
                 actor=actor,
                 started_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 planned=_batch_names(req_id), completed=[], eta_seconds=None,
                 prior_run=_prior_run_state(req_id))

    out_dir = role_output_dir(req_id)
    # -u: unbuffered child stdout, so the per-candidate lines stream instead of
    # arriving in 8KB bursts (tqdm's stderr was already unbuffered).
    # --req, not just --jd: two requisitions CAN point at the same JD file, and
    # resolving by path screened the wrong role's batch (UAT, 2026-08-26). The API
    # always knows exactly which requisition it is starting.
    cmd = [sys.executable, "-u", "-m", "resume_screener.cli", "run",
           "--jd", str(jd_ref), "--req", str(req_id), "--out", str(out_dir)]
    if jurisdiction:
        cmd += ["--jurisdiction", jurisdiction]
    if limit:
        cmd += ["--limit", str(limit)]
    if concurrency:
        cmd += ["--concurrency", str(concurrency)]

    threading.Thread(target=_run, args=(job_id, req_id, cmd, store, actor),
                     daemon=True).start()
    return _merge_live(store.read(job_id) or _public(job_id, req_id, {}))


def forget_req(req_id: str) -> int:
    """Drop every job record for a requisition; returns how many were dropped.

    Called when a requisition is DELETED. Without it the in-process store kept the
    old records forever (it only clears on restart), so recreating a requisition
    with the same id inherited a phantom run: `screen_lock` still saw a
    running/paused job, the batch stayed 409-locked for everyone, and the
    Candidates tab showed a run that no longer existed. That is the "screening
    state is stuck" report — the state belonged to a requisition that was gone."""
    dropped = 0
    store = _store()
    forget = getattr(store, "forget", None)
    if forget is not None:
        dropped = forget(req_id)
    # The live extras live beside the store and are keyed by job_id, so they have to
    # be swept by the same req_id.
    with _LIVE_LOCK:
        stale = [jid for jid, extra in _LIVE.items() if extra.get("req_id") == req_id]
        for jid in stale:
            _LIVE.pop(jid, None)
            proc = _PROCS.pop(jid, None)
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()  # a run for a deleted requisition must not keep going
                except OSError:
                    pass
    return dropped


def cancel_job(job_id: str, actor: str, intent: str = "cancel") -> tuple[str, dict | None]:
    """Ask a job to pause or cancel (2026-07-19 split). Both kill the subprocess
    (finished per-candidate work is cached either way); the difference is what
    the run means afterwards: PAUSE keeps the batch locked (the run intends to
    resume — the ordinary screen click picks it up from cache), CANCEL releases
    the lock so the batch can change and the next screen starts fresh.

    Returns (outcome, job): "stopping" (kill sent; the runner thread finalizes
    it as 'paused' or 'stopped' per the intent), "cancelled" (a PAUSED job was
    cancelled in place — no process to kill), "not_found", "not_running", or
    "not_here" (started by another process, so this one holds no handle)."""
    job = get_job(job_id)
    if job is None:
        return "not_found", None
    if job["status"] == "paused":
        if intent != "cancel":
            return "not_running", job
        # No subprocess exists — finalize the store record directly.
        state = {"status": "stopped", "done": job["done"], "total": job["total"],
                 "message": _cancel_message(actor), "error": ""}
        _store().save(job_id, job["req_id"], state)
        _emit_screen_cancelled(job["req_id"], actor or "someone", job_id,
                               done=job["done"], total=job["total"])
        return "cancelled", get_job(job_id)
    if job["status"] != "running":
        return "not_running", job
    with _LIVE_LOCK:
        proc = _PROCS.get(job_id)
        if proc is None:
            return "not_here", job
        # Record intent BEFORE the kill: the runner thread consults this after
        # wait() returns, so a killed run can never finalize as a plain error.
        _CANCELS[job_id] = (actor or "someone", "pause" if intent == "pause" else "cancel")
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — already exited; the runner still finalizes
        pass
    return "stopping", job


def _cancel_message(actor: str) -> str:
    return (f"Screening cancelled by {actor or 'someone'}. The batch is unlocked — "
            "finished work stays cached, so the next screen only pays for new résumés.")


def reap_orphans() -> int:
    """Recover from a previous process dying mid-run (called once at API startup).
    DB backend: flip leftover 'running' rows to the interrupted error. Both
    backends: any `.screen_job.json` sidecar a dead runner left behind becomes a
    screen_interrupted event, so the run history says what happened and who had
    started it. Never kills by pid — a pid may have been reused, and an orphaned
    run is benign (it finishes and lands results the cache then reuses).
    Best-effort; returns how many were reaped."""
    reaped = 0
    if _use_db():
        try:
            from resume_screener.db.repositories import jobs as jobs_repo
            from resume_screener.db.session import session_scope

            with session_scope() as session:
                reaped += jobs_repo.reap_running(session)
        except Exception:  # noqa: BLE001 — a reap failure must not block app startup
            pass
    try:
        for sidecar in Path("output").glob("*/.screen_job.json"):
            try:
                info = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            _emit_screen_interrupted(sidecar.parent.name, info)
            try:
                sidecar.unlink()
            except OSError:
                pass
            reaped += 1
    except Exception:  # noqa: BLE001
        pass
    return reaped


def _sidecar_path(req_id: str) -> Path:
    return role_output_dir(req_id) / ".screen_job.json"


def _run(job_id: str, req_id: str, cmd: list[str], store, actor: str = "") -> None:
    tail: list[str] = []
    state = {"status": "running", "done": 0, "total": 0, "message": "Starting…", "error": ""}

    def snapshot(**changes) -> None:
        state.update(changes)
        store.save(job_id, req_id, dict(state))

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", cwd=str(Path.cwd()),
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"Could not start screening: {exc}"
        snapshot(status="error", error=msg, message=msg)
        return

    with _LIVE_LOCK:
        _PROCS[job_id] = proc
        _LIVE.setdefault(job_id, {})["pid"] = proc.pid
    try:  # the sidecar lets a restarted app tell the user this run was interrupted
        sidecar = _sidecar_path(req_id)
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({
            "job_id": job_id, "pid": proc.pid, "actor": actor,
            "started_utc": _LIVE.get(job_id, {}).get("started_utc", ""),
        }), encoding="utf-8")
    except OSError:
        pass

    try:
        assert proc.stdout is not None
        completed: list[str] = []
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            # Per-candidate markers may be glued onto tqdm's newline-less bar, so
            # pull them from anywhere in the line, then strip them out before the
            # progress / tail parsing (keeps error surfacing from quoting them).
            names = _CANDIDATE.findall(line)
            if names:
                completed.extend(n.strip() for n in names)
                _live_update(job_id, completed=list(completed))
                line = _CANDIDATE.sub("", line).strip()
                if not line:
                    continue
            tail.append(line)
            tail[:] = tail[-40:]
            m = _PROGRESS.search(line)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                _live_update(job_id, eta_seconds=_eta_seconds(line))
                snapshot(done=done, total=total, message=f"Screening {done} of {total}…")
            elif line.startswith("Scored "):
                snapshot(message=line)

        code = proc.wait()
        canceller, intent = _pop_cancel(job_id)
        _live_update(job_id, eta_seconds=None)
        if code == 0:
            # A completed run beats a last-moment stop click — the results landed.
            message = next((t for t in reversed(tail) if t.startswith("Scored ")),
                           "Screening complete.")
            done = state["total"] if state["total"] else state["done"]
            snapshot(status="done", message=message, done=done)
            _emit_screen_completed(req_id, actor, job_id)
            _emit_decisions_superseded(req_id, actor, job_id)
        elif canceller and intent == "pause":
            snapshot(status="paused",
                     message=(f"Screening paused by {canceller}. The batch stays "
                              "locked — resume to pick up where it left off, or "
                              "cancel the run to unlock the batch."))
            _emit_screen_paused(req_id, canceller, job_id,
                                done=state["done"], total=state["total"])
        elif canceller:
            snapshot(status="stopped", message=_cancel_message(canceller))
            _emit_screen_cancelled(req_id, canceller, job_id,
                                   done=state["done"], total=state["total"])
        else:
            error = _friendly_error(tail) or f"Screening exited with code {code}."
            snapshot(status="error", error=error, message=error)
            _emit_screen_failed(req_id, actor, job_id, error)
    finally:
        with _LIVE_LOCK:
            _PROCS.pop(job_id, None)
        try:
            _sidecar_path(req_id).unlink(missing_ok=True)
        except OSError:
            pass


def _prior_run_state(req_id: str) -> dict:
    """Best-effort snapshot of the run a new screen will replace + how many
    decisions it carries — the P51 decisions_superseded event's inputs. Advisory
    only; never blocks a run."""
    try:
        from resume_screener.api import service

        meta = service.read_run_meta(req_id)
        if not meta:
            return {}
        return {"run_id": str(meta.get("run_id", "")),
                "decisions": len(service.role_decision_records(req_id))}
    except Exception:  # noqa: BLE001
        return {}


def _emit_decisions_superseded(req_id: str, actor: str, job_id: str) -> None:
    """A fresh run re-scores; the previous run's decisions are CARRIED FORWARD
    (2026-07-20) — a human decision belongs to the candidate, not the run, so a
    re-screen never wipes them (or their notes/comments). Ones whose score moved
    surface a 'reconfirm' badge on the review. Emit an event so the Audit
    Explorer's timeline keeps the story coherent."""
    try:
        from resume_screener.api.service import read_run_meta
        from resume_screener.events import emit_event

        prior = _LIVE.get(job_id, {}).get("prior_run") or {}
        old_run, n = prior.get("run_id"), int(prior.get("decisions") or 0)
        new_run = str((read_run_meta(req_id) or {}).get("run_id", ""))
        if not old_run or not n or not new_run or new_run == old_run:
            return
        emit_event("decisions_superseded", actor=actor, req_id=req_id,
                   summary=(f"{n} earlier decision(s) carried forward to the new "
                            "scores — reconfirm any the change moved"),
                   data={"old_run_id": old_run, "new_run_id": new_run, "decisions": n})
    except Exception:  # noqa: BLE001 — the spine must never fail a landed run
        pass


def _emit_screen_completed(req_id: str, actor: str, job_id: str = "") -> None:
    """Best-effort screen_completed event from the fresh run_meta (P29) — the
    event spine must never fail a job whose run already landed on disk. Counts +
    duration ride along so the run history can render the outcome without
    re-reading run_meta (which only survives for the LATEST run)."""
    try:
        from resume_screener.api.service import _run_meta_checklist_label, read_run_meta
        from resume_screener.events import emit_event

        meta = read_run_meta(req_id) or {}
        counts = meta.get("counts", {}) or {}
        scored = counts.get("scored", 0)
        pack = str(meta.get("policy_pack", ""))
        emit_event(
            "screen_completed",
            actor=actor or "system",
            req_id=req_id,
            summary=(f"Screened {scored} candidate{'s' if scored != 1 else ''} — "
                     f"{pack} hiring rules · {_run_meta_checklist_label(meta)} · "
                     f"run {meta.get('run_id')}"),
            data={"run_id": str(meta.get("run_id", "")), "pack": pack,
                  "rubric": str(meta.get("versions", {}).get("rubric", "")),
                  "job_id": job_id,
                  "counts": {"scored": scored,
                             "resumes_in": counts.get("resumes_in", 0),
                             "gate_passers": counts.get("gate_passers", 0),
                             "errors": counts.get("errors", 0)},
                  "duration_seconds": meta.get("duration_seconds"),
                  "started_utc": str(meta.get("started_utc", ""))},
        )
    except Exception:  # noqa: BLE001 — never fail the job on the event append
        pass


def _emit_screen_cancelled(req_id: str, actor: str, job_id: str, *,
                           done: int, total: int) -> None:
    """Kept on the historical `screen_stopped` kind so the run history and the
    Audit Explorer read one vocabulary; the summary says cancel."""
    try:
        from resume_screener.events import emit_event

        of_part = f" after {done} of {total} résumés" if total else ""
        emit_event("screen_stopped", actor=actor, req_id=req_id,
                   summary=(f"Screening cancelled by {actor}{of_part} — the batch is "
                            "unlocked; finished work stays cached for the next screen"),
                   data={"job_id": job_id, "done": done, "total": total,
                         "intent": "cancel"})
    except Exception:  # noqa: BLE001
        pass


def _emit_screen_paused(req_id: str, actor: str, job_id: str, *,
                        done: int, total: int) -> None:
    try:
        from resume_screener.events import emit_event

        of_part = f" after {done} of {total} résumés" if total else ""
        emit_event("screen_paused", actor=actor, req_id=req_id,
                   summary=(f"Screening paused by {actor}{of_part} — the batch stays "
                            "locked until the run resumes or is cancelled"),
                   data={"job_id": job_id, "done": done, "total": total,
                         "intent": "pause"})
    except Exception:  # noqa: BLE001
        pass


def _emit_screen_failed(req_id: str, actor: str, job_id: str, error: str) -> None:
    try:
        from resume_screener.events import emit_event

        emit_event("screen_failed", actor=actor or "system", req_id=req_id,
                   summary=f"Screening didn't finish — {error[:160]}",
                   data={"job_id": job_id, "error": error[:300]})
    except Exception:  # noqa: BLE001
        pass


def _emit_screen_interrupted(req_id: str, info: dict) -> None:
    try:
        from resume_screener.events import emit_event

        started_by = str(info.get("actor") or "") or "someone"
        emit_event("screen_interrupted", actor="system", req_id=req_id,
                   summary=(f"Screening was interrupted by an app restart — it was "
                            f"started by {started_by}. Screening again picks up "
                            "where it left off."),
                   data={"job_id": str(info.get("job_id", "")),
                         "started_by": str(info.get("actor", "")),
                         "started_utc": str(info.get("started_utc", ""))})
    except Exception:  # noqa: BLE001
        pass


def _friendly_error(tail: list[str]) -> str:
    """Surface the pipeline's own '✗ …' line when it failed (config/auth/rubric)."""
    for line in reversed(tail):
        if line.startswith("✗"):
            return line.lstrip("✗ ").strip()
    return tail[-1] if tail else ""
