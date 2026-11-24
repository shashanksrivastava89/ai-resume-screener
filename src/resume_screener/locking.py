"""Cross-process file locks for shared mutable files (P26, Addendum 08).

The hosted app is ONE replica by design, but still several writers: the API
process (decisions, governance publishes) and the `screener run` subprocess it
spawns (the private candidate map), plus any operator CLI session. A plain
threading.Lock only covers one process, so every write to a shared mutable file
— decisions.jsonl, audit_log.jsonl, registry/pack YAML, review_status.json, the
private map — goes through `file_lock()`.

Mechanics: the lock is taken on a `<name>.lock` SIDECAR, never the data file
itself, because the atomic write pattern (write temp → os.replace) swaps the
data file's inode out from under any lock held on it. Sidecars are tiny, live
next to the data (so they're on the same Azure Files share), and are harmless
to delete when no writer is active.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import portalocker

# Waiting writers block up to this long before erroring — far beyond any real
# append/YAML-dump, so a timeout means a crashed holder or a hung share.
_TIMEOUT_SECONDS = 30


@contextmanager
def file_lock(path: str | Path) -> Iterator[None]:
    """Exclusive cross-process lock guarding writes to `path` (via its sidecar).

    Also the one place every shared-file WRITE passes through — so releasing it is
    where the request-scoped read memo is dropped (reqcache): a read that follows
    a write in the same request must see the write, and no writer has to remember
    to invalidate anything."""
    from resume_screener import reqcache

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sidecar = target.with_name(target.name + ".lock")
    try:
        with portalocker.Lock(str(sidecar), mode="a", timeout=_TIMEOUT_SECONDS):
            yield
    finally:
        reqcache.clear()


def try_file_lock(path: str | Path) -> portalocker.Lock | None:
    """Non-blocking variant: the acquired lock (caller must .release()), or None
    when another process holds it RIGHT NOW.

    file_lock() waits up to 30s, which is right for short appends but wrong for
    probing a long-lived holder — the batch-preparation worker holds its lock for
    minutes, and "is a worker alive?" must answer immediately, not after a 30s
    stall on every poll. The OS releases the lock when the holder dies, so
    acquirability doubles as the liveness signal."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sidecar = target.with_name(target.name + ".lock")
    lock = portalocker.Lock(str(sidecar), mode="a", timeout=0, fail_when_locked=True)
    try:
        lock.acquire()
    except portalocker.exceptions.BaseLockException:
        return None
    return lock
