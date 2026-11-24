"""Repository layer — the ONLY place that talks to ORM models.

Pattern (kept consistent as domains are added in later phases):
- every function takes an explicit `session: Session` as its first argument and
  never opens its own transaction (the caller owns the unit of work via
  `session_scope()` or the FastAPI `get_session` dependency);
- functions accept/return plain values or the app's Pydantic contracts (§4),
  so engine/API code stays contract-oriented, not ORM-aware;
- integrity rules that must always hold (append-only decision/audit logs, the
  required-reason rule, locked base exclusions) are enforced HERE, not as rigid
  DB constraints — so the schema can evolve freely.

Phase 1 ships the region + identity repos (the region base framework); each later
phase adds its domain's repo (decisions, audit, runs, jobs, config-as-data).
"""

from __future__ import annotations

from resume_screener.db.repositories import (
    decisions,
    governance,
    identity,
    jobs,
    policy,
    regions,
    registry,
    requisitions,
    rubric,
    runs,
)

__all__ = [
    "decisions", "governance", "identity", "jobs", "policy", "regions", "registry",
    "requisitions", "rubric", "runs",
]
