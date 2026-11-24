"""Test-session database isolation.

The suite is offline and must behave like CI, where no region framework is
seeded — the P38 region-access enforcement then degrades to unrestricted (the
documented "unseeded framework = everything visible" pin). A developer's local
Postgres — the default DATABASE_URL — may however be seeded, which would lock the
synthetic SSO users the API tests use out of every requisition (they hold no
region grant).

Point the ambient DATABASE_URL at a throwaway in-memory SQLite with no tables, so
any un-isolated DB read (region_context, the file-first feature-flag merge, …)
raises and degrades gracefully, exactly as it does when no database is reachable.
Every DB-backed repository test wires its OWN engine via tests/dbharness +
monkeypatch, so this only affects code paths that would otherwise reach the
ambient database. TEST_DATABASE_URL (dbharness's opt-in Postgres pass) is a
separate variable and is untouched.
"""

import os

# Set before any test triggers load_db_settings(); harmless if a test overrides
# the engine directly (repo tests all do).
os.environ["DATABASE_URL"] = "sqlite://"

# Batch preparation runs its worker INLINE (same run_worker(), called directly
# instead of a detached subprocess) so route tests see uploads land
# deterministically. Tests that exercise the asynchronous contract itself
# (staging, reconcile, crash recovery) monkeypatch this back to "0".
os.environ["BATCH_PREP_INLINE"] = "1"

# Decision verdicts are sealed only when a key is configured (fieldcrypto). The suite
# runs with none, so every decision test sees the plain, zero-infra behaviour unless
# it injects a key provider itself.
os.environ.pop("DECISION_KEY_SECRET", None)

# Per-person rate limits (api/ratelimit.py) are off for the suite — hundreds of
# requests from one synthetic user in seconds is the test suite, not abuse. The rate
# limit tests turn them back on.
os.environ.setdefault("RATE_LIMIT", "off")

# The fairness check's intent screen (prefcheck_semantic) calls the model. The suite
# never reaches Azure, so it is off — set outright, not defaulted, because the app
# loads .env at import and a developer's shell may have it on. Its tests fake the
# model and switch it back on.
os.environ["PREF_SEMANTIC_CHECK"] = "off"


# --- caches whose key does not say WHICH world they came from --------------------
#
# Most of the app's cross-request caches are keyed on file state, and every test gets
# a fresh tmp_path, so their keys differ per test and they cannot leak. Three are not
# like that, and each is a genuine cross-test hazard:
#
#   * region access is keyed on the EMAIL alone, behind a short TTL;
#   * the decision log and the resolved policy packs are keyed on a DB version
#     stamp — and two different test databases each holding one decision produce the
#     SAME stamp, so one test's rows would answer another test's question.
#
# Found by test_region_seed, which passed alone and failed inside its own file. A
# cache that makes the suite order-dependent costs more than it saves.
#
# Only these three. Clearing the file-state caches as well was correct and took the
# suite from 80 seconds to 30 minutes, because tests legitimately share that work —
# the cost of a cache is paid on the miss, and there is no reason to force one.
import pytest


@pytest.fixture(autouse=True)
def _drop_world_agnostic_caches():
    def clear():
        from resume_screener import azure_clients, config, feedback, policy
        from resume_screener.api import service

        service.invalidate_region_context()
        policy.invalidate_packs()
        feedback.invalidate_decisions()
        # The probed OpenAI client and the cached AI keys are process-wide and keyed on
        # nothing a test controls — a test that builds one must not hand it to the next.
        azure_clients.reset()
        config.invalidate_api_keys()
        from resume_screener import fieldcrypto
        from resume_screener.api import ratelimit

        fieldcrypto.set_key_provider(None)
        ratelimit.reset()

    clear()
    yield
    clear()
