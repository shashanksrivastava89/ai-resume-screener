"""Request-scoped memoisation for the pure readers behind every API view.

Why this exists (2026-09-05). Profiling the dashboard against a pilot-shaped root
showed 2,952 filesystem calls per load: the requisition sidecar read ~10x per
requisition, run_meta ~7x, the decisions log ~7x, the DB-settings object built
~400x. Each of those is one round trip to the Azure Files share on the hosted app
(3-15 ms), so a request that takes 90 ms on a laptop took 5-30 s for a recruiter.
The readers are all pure functions of on-disk state — within ONE request the
answer cannot change — so the fix is to answer each (function, args) once per
request.

Design constraints, in order:

1. **Never stale.** The store lives in a ContextVar that the HTTP middleware
   creates per request and drops at the end. Outside a request (CLI, tests that
   call service functions directly, the screening worker) there is no store and
   every call goes straight through, byte-identical to before.
2. **Writes clear it.** A request that writes then reads (POST /decisions returns
   fresh counts) must see its own write. Every shared-file write in this codebase
   passes through locking.file_lock, and every DB write through db.session's
   commit; both call clear(). Nothing has to remember to invalidate.
3. **Explicit keys.** The arguments are often unhashable Pydantic models (a
   Requisition, an AppConfig), so each memoised function names its own key
   rather than hashing *args — and the key must identify the on-disk state read,
   not the Python object.

This is deliberately NOT a cross-request cache: those need validity checks, and
this module's whole point is that it needs none. Cross-request caches (decisions
by file state, candidate records by run state, the requisition list by directory
state) stay where they are and keep their own invalidation.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Hashable
from contextvars import ContextVar, Token
from typing import Any, TypeVar

_STORE: ContextVar[dict[Hashable, Any] | None] = ContextVar("request_memo", default=None)

F = TypeVar("F", bound=Callable[..., Any])

# A sentinel distinct from None, since None is a legitimate cached answer
# (read_run_meta returns None for a role that has never run).
_MISSING = object()


def begin() -> Token:
    """Open a store for the current context (the middleware calls this)."""
    return _STORE.set({})


def end(token: Token) -> None:
    _STORE.reset(token)


def active() -> bool:
    return _STORE.get() is not None


def clear() -> None:
    """Drop everything memoised so far in this request — called after any write, so
    a read that follows a write in the same request sees the new state."""
    store = _STORE.get()
    if store:
        store.clear()


def memo(key: Callable[..., Hashable]) -> Callable[[F], F]:
    """Memoise `fn` for the rest of the current request under `key(*args, **kwargs)`.

    `key` returns the identity of the ON-DISK STATE the function reads (a path, a
    req_id, a user), never the Python objects passed in. When no request store is
    active the function runs untouched."""
    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            store = _STORE.get()
            if store is None:
                return fn(*args, **kwargs)
            k = (fn.__module__, fn.__qualname__, key(*args, **kwargs))
            hit = store.get(k, _MISSING)
            if hit is not _MISSING:
                return hit
            value = fn(*args, **kwargs)
            store[k] = value
            return value
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]
    return decorate
