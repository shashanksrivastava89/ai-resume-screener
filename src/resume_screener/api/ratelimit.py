"""Per-person rate limits on the routes that spend money or move data in bulk (2026-10-14).

A token bucket per (action, person): a full bucket allows a burst, then refills at the
budget's rate. Over budget, the request is answered 429 with a Retry-After and a plain
sentence — nothing is started. The point is to stop a stuck script, a looping tab or a
misused account from running up model costs or pulling every résumé out at once, not
to slow anyone down: the budgets sit well above what a person clicking does.

Budgets (count per minutes; override with RATE_LIMIT_<ACTION>="count/minutes"):

| action        | default  | what one request is                                         |
|---------------|----------|-------------------------------------------------------------|
| upload        | 200 / 10 | one upload group (the client sends ≤ 20 files per request), or a folder import |
| screen        | 10 / 10  | a screening run                                             |
| rubric_draft  | 10 / 10  | an AI checklist draft                                       |
| jd_parse      | 20 / 10  | an uploaded JD read by the AI                               |
| export        | 30 / 10  | a shortlist CSV / ZIP, a batch ZIP, an audit report CSV     |
| handoff       | 20 / 10  | an Ask-Claude bundle                                        |
| pref_check    | 30 / 10  | a preference checked or added (the AI fairness check runs)  |
| checklist_check | 60 / 10 | a checklist row checked, or a checklist published          |
| people_search | 120 / 10 | a name looked up for the HM picker or an @-mention          |
| api           | 600 / 1  | ANY /api/ request (app._general_budget_429, in the middleware) |

The limiter runs AFTER a route's capability check (it is the last dependency in each
signature, and FastAPI resolves them in order), so someone without access gets their
403 and never touches a bucket. RATE_LIMIT=off switches every limit off (the test
suite's default). Buckets live in this process's memory: one App Service instance
(B1) is one limiter, which is the deployment we have.
"""

from __future__ import annotations

import math
import os
import threading
import time

from fastapi import HTTPException, Request

DEFAULTS: dict[str, tuple[int, float]] = {
    "upload": (200, 10),
    "screen": (10, 10),
    "rubric_draft": (10, 10),
    "jd_parse": (20, 10),
    "export": (30, 10),
    "handoff": (20, 10),
    "pref_check": (30, 10),
    "checklist_check": (60, 10),
    "people_search": (120, 10),
    # Every /api/ request, per person (the middleware's general budget, 2026-10-16).
    "api": (600, 1),
}
MESSAGE = ("You've done that a lot in the last few minutes — please wait a minute and try "
           "again.")
_IDLE_SECONDS = 3600.0

_clock = time.monotonic  # tests replace this
_lock = threading.Lock()
_buckets: dict[tuple[str, str], "TokenBucket"] = {}
_last_sweep = 0.0


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float, now: float):
        self.capacity = capacity
        self.refill = refill_per_second
        self.tokens = capacity
        self.updated = now

    def take(self, now: float) -> float:
        """Spend a token: 0 when one was available, else seconds until one will be."""
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.refill


def enabled() -> bool:
    return (os.environ.get("RATE_LIMIT") or "on").strip().lower() not in ("off", "0", "false", "no")


def budget(action: str) -> tuple[int, float]:
    """(count, minutes) for an action — the environment's override when it parses."""
    count, minutes = DEFAULTS[action]
    raw = (os.environ.get(f"RATE_LIMIT_{action.upper()}") or "").strip()
    if raw:
        try:
            c, m = raw.split("/", 1)
            if int(c) > 0 and float(m) > 0:
                count, minutes = int(c), float(m)
        except ValueError:
            pass  # a malformed override keeps the default rather than disabling the limit
    return count, minutes


def check(action: str, who: str) -> float:
    """Spend one of `who`'s tokens for `action`: 0 when allowed, else seconds to wait."""
    global _last_sweep
    count, minutes = budget(action)
    now = _clock()
    with _lock:
        if now - _last_sweep > 60:
            _last_sweep = now
            for key in [k for k, b in _buckets.items() if now - b.updated > _IDLE_SECONDS]:
                del _buckets[key]
        refill = count / (minutes * 60)
        bucket = _buckets.get((action, who))
        if bucket is None or (bucket.capacity, bucket.refill) != (count, refill):
            bucket = _buckets[(action, who)] = TokenBucket(count, refill, now)
        return bucket.take(now)


def limited(action: str):
    """A route dependency enforcing `action`'s budget for the signed-in person."""
    if action not in DEFAULTS:
        raise ValueError(f"No rate-limit budget for {action!r}")

    def dependency(request: Request) -> None:
        if not enabled():
            return
        from resume_screener import rbac

        try:
            who = (rbac.get_identity(request.headers)[0] or "").strip().lower()
        except Exception:  # noqa: BLE001 — the capability guard has already answered
            who = ""
        wait = check(action, who or "anonymous")
        if wait > 0:
            raise HTTPException(429, MESSAGE, headers={"Retry-After": str(max(1, math.ceil(wait)))})

    return dependency


def reset() -> None:
    with _lock:
        _buckets.clear()
