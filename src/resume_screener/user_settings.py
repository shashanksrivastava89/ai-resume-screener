"""Per-person preferences the app remembers (2026-09-12).

WHY NOT SOMEWHERE THAT ALREADY EXISTS.

* Not `people.jsonl`. That store LEARNS about a person — a name from a sign-in, a
  title from the directory — and it is trust-ranked, so Microsoft Graph overwrites
  what a sign-in taught. A choice someone made is not a learning, and a directory
  refresh must never silently overwrite it. The people record is also served to
  every recruiter through `/api/people/directory`; a personal setting is not.
* Not `app_settings.json`. That is one app-wide blob rewritten whole.
* Not a column on `app_user`. A display preference is not worth an Alembic
  migration plus the SQLite test harness, and it would split identity-adjacent
  state across two stores for no gain.

SHAPE. Append-only JSONL, latest line per (email, key) wins — the same contract as
`people.jsonl` and `decisions.jsonl`, for the same reason: a half-written line at
the end of a crash never takes the file down with it.

A DB backend is the obvious later seam (`USER_SETTINGS_BACKEND`), and deliberately
not built now: nothing here needs to be queried across users.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resume_screener import reqcache
from resume_screener.locking import file_lock

SETTINGS_LOG = Path("signals") / "user_settings.jsonl"

# path -> (size, mtime_ns, {(email, key): value}). Append-only, so a stale hit is
# impossible: any write moves the size.
_CACHE: dict[str, tuple[int, int, dict[tuple[str, str], Any]]] = {}


def _key(email: str | None) -> str:
    return str(email or "").strip().lower()


def _parsed(path: Path) -> dict[tuple[str, str], Any]:
    try:
        state = path.stat()
    except OSError:
        return {}
    cache_key = str(path.resolve())
    hit = _CACHE.get(cache_key)
    if hit is not None and hit[0] == state.st_size and hit[1] == state.st_mtime_ns:
        return hit[2]
    out: dict[tuple[str, str], Any] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a half-written line never takes the store down
        email, name = _key(record.get("email")), str(record.get("key") or "")
        if email and name:
            out[(email, name)] = record.get("value")
    _CACHE[cache_key] = (state.st_size, state.st_mtime_ns, out)
    return out


@reqcache.memo(key=lambda log_path=SETTINGS_LOG: str(log_path))
def _all(log_path: str | Path = SETTINGS_LOG) -> dict[tuple[str, str], Any]:
    return _parsed(Path(log_path))


def get(email: str, key: str, default: Any = None,
        log_path: str | Path = SETTINGS_LOG) -> Any:
    value = _all(log_path).get((_key(email), key), default)
    return default if value is None else value


def set(email: str, key: str, value: Any,  # noqa: A001 — the obvious name for this
        log_path: str | Path = SETTINGS_LOG) -> None:
    """Record one person's choice. `value=None` clears it."""
    address = _key(email)
    if not address or not key:
        return
    record = {"email": address, "key": key, "value": value,
              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
