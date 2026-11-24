"""Performance budgets — the fs-op ceilings the recruiter-facing endpoints must stay
under (2026-09-05).

Why filesystem OPERATIONS and not milliseconds: on the hosted app every open/stat/
scandir is a round trip to the Azure Files share (3-15 ms each), and the same code
on a laptop's SSD is a hundred times faster — so wall time in CI says nothing about
what a recruiter waits. Before the fix the dashboard made 2,952 such calls per load
(≈ 15-30 s hosted, matching the platform's own metrics); after it, 268.

These are ceilings with headroom, not the measured values, so ordinary feature
work does not trip them — a regression back towards per-request directory walks
will. The synthetic root and the counters come from scripts/perf_bench.py so this
test and the benchmark measure the same thing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import perf_bench  # noqa: E402  (scripts/perf_bench.py)

from resume_screener import dashboard_index  # noqa: E402
from resume_screener.api import service  # noqa: E402

N_REQS, BIG, MEDIUM = 10, 60, 20


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    root = tmp_path_factory.mktemp("perfroot")
    cwd = os.getcwd()
    saved = {k: os.environ.get(k) for k in ("DEV_USER", "DEV_ROLE", "DATA_ROOT", "APP_MODE")}
    os.environ.update(APP_MODE="dev", DEV_USER="arjun.test@example.com", DEV_ROLE="SuperAdmin")
    os.environ.pop("DATA_ROOT", None)
    os.chdir(root)
    try:
        perf_bench.build_root(root, reqs=N_REQS, big=BIG, medium=MEDIUM,
                              decisions_total=400, events_total=200)
        perf_bench._install_counters()
        from fastapi.testclient import TestClient
        from resume_screener.api.app import app

        client = TestClient(app)
        # Warm the cross-request caches (records by run state, the requisition list,
        # parsed logs) exactly as a running server would after its first requests.
        for _label, path in perf_bench.ENDPOINTS:
            client.get(path)
        yield client
    finally:
        os.chdir(cwd)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _ops(client, path: str) -> tuple[int, int]:
    """(fs ops, sql statements) for one WARM call.

    The warm-up is explicit. These budgets used to depend on some earlier test in the
    session having filled the process-level caches — invisible coupling that made a
    passing run a matter of collection order, and that broke the moment conftest
    started clearing those caches between tests (2026-09-10). What is being measured
    is the steady state, so the steady state is what the test establishes."""
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])
    perf_bench.COUNTERS.reset()
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:200])
    return perf_bench.COUNTERS.fs, perf_bench.COUNTERS.sql


def test_dashboard_does_not_scale_with_requisitions(bench):
    """The dashboard is the surface that blocks everyone at 500 requisitions, so its
    cost must stop growing with the list (2026-09-10).

    Cards come from the remembered index, and a load validates at most `budget` rows
    against four stats each — so the ceiling is the budget, NOT the requisition count.
    Before the index this was ~7 share round trips per requisition, every load."""
    fs, _ = _ops(bench, "/api/roles")
    ceiling = 4 * dashboard_index.DEFAULT_BUDGET + 60
    assert fs <= ceiling, (
        f"/api/roles made {fs} fs ops for {N_REQS} requisitions (ceiling {ceiling}); "
        "this grows with the list again")


def test_opening_a_requisition_is_a_handful_of_reads(bench):
    fs, _ = _ops(bench, f"/api/roles/{perf_bench.BIG}")
    assert fs <= 60, f"opening a requisition made {fs} fs ops (was 971)"


def test_the_workspace_poll_is_nearly_free(bench):
    """This fires every 2 s while a run is live — it must not re-walk anything."""
    fs, _ = _ops(bench, f"/api/roles/{perf_bench.BIG}/screen/active")
    assert fs <= 30, f"the workspace poll made {fs} fs ops (was 196)"


def test_the_dashboard_poll_is_nearly_free(bench):
    fs, _ = _ops(bench, "/api/screen/active")
    assert fs <= 20, f"the dashboard poll made {fs} fs ops"


def test_a_read_never_costs_a_flag_file_parse(bench):
    """The capability check runs on EVERY request — static files and the health probe
    included — and on 2026-09-07 it stopped being a bare env read. Parsing
    features.yaml (four times) and rbac.yaml per request was 35 ms of pure overhead
    on the request path."""
    import resume_screener.feature_flags as ff
    import resume_screener.rbac as rbac

    bench.get("/api/me")            # warm both caches
    parses = {"n": 0}
    real_parse, real_rbac = ff._parse_features, rbac._parse_rbac
    ff._parse_features = lambda path: (parses.__setitem__("n", parses["n"] + 1),
                                       real_parse(path))[1]
    rbac._parse_rbac = lambda path: (parses.__setitem__("n", parses["n"] + 1),
                                     real_rbac(path))[1]
    try:
        bench.get("/api/me")
        bench.get("/api/roles")
    finally:
        ff._parse_features, rbac._parse_rbac = real_parse, real_rbac
    assert parses["n"] == 0, f"two requests re-parsed the flag/rbac files {parses['n']} times"


def test_the_health_probe_resolves_no_identity(bench):
    """14% of hosted traffic. It reaches no authorised data, and the route guards are
    the enforcement point regardless."""
    fs, sql = _ops(bench, "/api/health")
    assert fs <= 2 and sql == 0, f"the health probe cost {fs} fs ops and {sql} queries"


def test_review_does_not_scale_with_candidates(bench):
    """The review payload is built from cached records; loading it must not touch
    a file per candidate."""
    fs, _ = _ops(bench, f"/api/roles/{perf_bench.BIG}/review")
    assert fs <= 60, f"review made {fs} fs ops for {BIG} candidates (was 673)"


def test_every_response_carries_server_timing(bench):
    """The one latency signal a person can see without platform access: the
    browser's Network panel shows Server-Timing per request."""
    r = bench.get("/api/roles")
    assert r.headers.get("Server-Timing", "").startswith("app;dur=")


def test_a_write_is_visible_to_the_same_request_that_made_it(bench, tmp_path):
    """The request memo must never serve a pre-write read after a write: the
    decision route records then returns fresh counts. Exercised at the unit level
    against the memo + the file lock that clears it."""
    from resume_screener import reqcache
    from resume_screener.locking import file_lock

    calls = {"n": 0}

    @reqcache.memo(key=lambda p: str(p))
    def read(p: Path) -> str:
        calls["n"] += 1
        return p.read_text(encoding="utf-8") if p.exists() else ""

    target = tmp_path / "state.txt"
    token = reqcache.begin()
    try:
        assert read(target) == "" and calls["n"] == 1
        assert read(target) == "" and calls["n"] == 1          # memoised
        with file_lock(target):
            target.write_text("v2", encoding="utf-8")
        assert read(target) == "v2" and calls["n"] == 2        # cleared by the write
    finally:
        reqcache.end(token)
    # Outside a request there is no memo at all — every call goes to disk.
    assert read(target) == "v2" and calls["n"] == 3


# --- the governed-config reads (2026-09-15) -------------------------------------
#
# Added after a hosted slow-request census named all three and this file measured
# none of them: /api/locations at 849 ms median (it sits on the dashboard, Overview
# AND the New-requisition form) and /api/scoring-model at 19.6 s, the slowest request
# in the log that was not a model call. An endpoint no benchmark calls is an endpoint
# nobody optimises.


def test_the_scoring_page_does_not_re_walk_the_quarters_candidates(bench):
    """`median_recent_signal_move` — one display fragment — read the FULL record of
    every candidate screened in the last quarter, on EVERY load of the Scoring page.
    On the pilot's share, cold, that was the 19.6 s in the hosted log.

    The count of records read is what this pins, not an op total: the walk is the
    cost, and it is the same walk whichever backend the deployment runs."""
    bench.get("/api/scoring-model")          # the first load pays the walk, once
    calls = {"n": 0}
    real = service.read_candidate_records
    service.read_candidate_records = lambda *a, **k: (
        calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1]
    try:
        bench.get("/api/scoring-model")
    finally:
        service.read_candidate_records = real
    assert calls["n"] == 0, (
        f"a second Scoring page load re-read {calls['n']} requisitions' candidate "
        "records for the median-signal-move fragment")


def test_the_locations_read_does_not_re_parse_a_pack_per_pack(bench):
    """/api/locations resolves every pack, and each resolve re-read and re-validated
    base.yaml: 28 of its 52 filesystem ops were one loader, for governed data that
    changes only through a publish."""
    fs, _ = _ops(bench, "/api/locations")
    assert fs <= 45, f"/api/locations made {fs} fs ops (was 52; 38 after the pack cache)"


def test_resolving_every_pack_asks_the_packs_table_once(bench):
    """The DB branch asked "have the packs changed?" once per JURISDICTION, inside
    the session each resolve opened — 7 of /api/locations' 8 queries, for a table
    that cannot change mid-request.

    Pinned here rather than in the DB-mode bench because it needs no Postgres: the
    session and the stamp query are both stubbed, so what is being measured is the
    memo itself — how many times a request reaches the database for an answer that
    cannot change inside one."""
    import contextlib

    from resume_screener import policy, reqcache
    from resume_screener.db.repositories import policy as policy_repo
    from resume_screener.db import session as db_session

    calls = {"n": 0}

    def fake_stamp(session):
        calls["n"] += 1
        return (1, 1, None)

    @contextlib.contextmanager
    def fake_scope():
        yield object()

    real_stamp, real_scope = policy_repo.table_stamp, db_session.session_scope
    policy_repo.table_stamp, db_session.session_scope = fake_stamp, fake_scope
    token = reqcache.begin()
    try:
        for _ in range(5):                      # five packs resolved in one request
            policy._pack_table_stamp()
    finally:
        reqcache.end(token)
        policy_repo.table_stamp, db_session.session_scope = real_stamp, real_scope
    assert calls["n"] == 1, (
        f"five resolves asked the packs table for its stamp {calls['n']} times")
