"""Synthetic-volume API benchmark — the numbers behind every performance change.

    uv run --no-sync python scripts/perf_bench.py            # table to stdout
    uv run --no-sync python scripts/perf_bench.py --json out.json --runs 7

Builds a throwaway root shaped like the pilot — 25 requisitions, one with 235
screened candidates, ~1,700 append-only decisions, ~600 spine events — serves the
API in-process (FastAPI TestClient, dev identity, no network) and measures each
recruiter-facing endpoint the way it is actually paid for:

  wall ms   median wall time of `--runs` calls after one warm-up
  cpu ms    process CPU per call — the 1-vCPU B1 plan's real budget
  fs ops    open + stat + scandir calls per call. On the hosted app every one of
            these is a round trip to the Azure Files SMB share (~3–15 ms each), so
            a local run that shows 400 ops is a hosted request that takes seconds
            even when the local wall time reads 40 ms.
  sql       statements executed (0 in the default file-backed mode; the DB-backed
            deployment does the same work as queries, so op-count fixes carry over)

Synthetic data only — nothing here reads data/, output/ or .cache/ (CLAUDE.md §6.12).
Record shapes mirror report._candidate_json / models.Decision / events.emit_event so
the builders under test see exactly what a real run leaves behind.
"""

from __future__ import annotations

import argparse
import builtins
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

BIG = "fin-analyst-gurugram"       # the 235-candidate requisition
MEDIUM = "sales-manager-mumbai"    # a 40-candidate one
ACTOR = "bhavika.test@example.com"

REQUIREMENTS = [
    ("r01", "GL accounting and month-end close", "skill", "must_have", 1.0),
    ("r02", "Ind AS / IFRS reporting", "skill", "must_have", 1.0),
    ("r03", "SAP FI/CO", "skill", "nice_to_have", 0.6),
    ("r04", "Chartered Accountant (CA)", "certification", "hard_gate", 1.0),
    ("r05", "5+ years in a manufacturing finance role", "experience", "must_have", 0.9),
    ("r06", "GST / TDS compliance", "skill", "nice_to_have", 0.5),
    ("r07", "Power BI or Tableau", "skill", "nice_to_have", 0.4),
    ("r08", "Stakeholder management across plants", "experience", "nice_to_have", 0.5),
]


# --- root builder -------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _rubric_yaml(stem: str, title: str) -> str:
    lines = [f"role_title: {title}", f"jd_ref: jds/{stem}.md", "version: 1", "requirements:"]
    for rid, text, typ, kind, weight in REQUIREMENTS:
        lines.append(f"  - {{id: {rid}, text: '{text}', type: {typ}, kind: {kind}, weight: {weight}}}")
    return "\n".join(lines) + "\n"


def _record(cid: str, i: int, title: str) -> dict:
    """One per-candidate JSON in the shape report._candidate_json writes."""
    eligible = i % 9 != 0                       # ~11% fail the hard gate
    coverage = 0.35 + (i * 37 % 60) / 100.0     # 0.35..0.94, deterministic spread
    base = round(100 * coverage, 1)
    adj = ((i * 7) % 9) - 4 if eligible else 0  # −4..+4
    final = max(0.0, min(100.0, base + adj))
    band = "Strong" if final >= 88 else "Possible" if final >= 60 else "Not a fit"
    matches = ["full", "partial", "none"]
    findings = [{
        "requirement_id": rid, "requirement_text": text,
        "match": matches[(i + k) % 3],
        "evidence": f"Led {text.lower()} for a regional plant network" if (i + k) % 3 != 2 else None,
    } for k, (rid, text, _t, _k, _w) in enumerate(REQUIREMENTS)]
    must_missing = [f["requirement_id"] for f in findings
                    if f["match"] == "none" and f["requirement_id"] in ("r01", "r02", "r05")]
    layer2 = None
    if eligible:
        layer2 = {"candidate_id": cid,
                  "adjustments": [{"signal_id": "regulated_environment_exposure", "points": adj,
                                   "evidence": "Handled statutory audits under the Companies Act",
                                   "rationale": "Direct regulated-reporting exposure"}] if adj else [],
                  "proposed_signals": [], "narrative": "Solid plant-finance background."}
    return {
        "score": {
            "candidate_id": cid, "role_title": title, "jurisdiction": "IN",
            "layer1": {"candidate_id": cid, "rubric_version": 1,
                       "gate_flags": [] if eligible else ["r04"],
                       "must_haves_missing": must_missing,
                       "coverage_score": round(coverage, 3), "base_score": base,
                       "eligible": eligible,
                       "reasons": [] if eligible else ["Hard gate failed: Chartered Accountant (CA)"]},
            "layer2": layer2, "critic": None,
            "final_score": final, "fit_band": band if eligible else "Not a fit",
            "confidence": "medium", "summary_rationale": "Covers the core close and reporting work.",
            "preference_adjustments": [],
        },
        "findings": {"candidate_id": cid, "findings": findings},
        "features": None,
        "suspended_adjustments": [],
        "location": ({"place": "Gurugram", "stated": "Gurgaon, Haryana", "km": 0.0,
                      "resolved": True} if i % 3 else None),
        "extraction": {"confidence": "medium", "extractor": "layout", "pages": 2},
    }


def build_root(root: Path, *, reqs: int = 25, big: int = 235, medium: int = 40,
               decisions_total: int = 1700, events_total: int = 600) -> dict:
    """Lay down a pilot-shaped DATA_ROOT. Returns {req_id: candidate_count}."""
    for name in ("config.yaml", "rbac.yaml"):
        shutil.copyfile(REPO / name, root / name)
    (root / "policies").mkdir()
    for pack in sorted((REPO / "policies").glob("*.yaml")):
        shutil.copyfile(pack, root / "policies" / pack.name)
    (root / "signals").mkdir()
    for y in sorted((REPO / "signals").glob("*.yaml")):   # committed config only, never .jsonl
        shutil.copyfile(y, root / "signals" / y.name)
    (root / "jds").mkdir()
    (root / "output" / "feedback").mkdir(parents=True)

    from resume_screener.ats.local_folder import save_req_meta
    from resume_screener.cache import content_hash
    from resume_screener.checklist_approval import record_publish
    from resume_screener.models import candidate_id_for
    from resume_screener.scoring_model import scoring_model_stamp

    policy_files = {"base": content_hash((root / "policies" / "base.yaml").read_bytes())[:12],
                    "IN": content_hash((root / "policies" / "IN.yaml").read_bytes())[:12]}
    t0 = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)

    sizes: dict[str, int] = {}
    for n in range(reqs):
        if n == 0:
            stem, title, count = BIG, "Financial Analyst — Gurugram", big
        elif n == 1:
            stem, title, count = MEDIUM, "Sales Manager — Mumbai", medium
        else:
            stem, title = f"req-{n:02d}", f"Requisition {n:02d}"
            count = [0, 0, 6, 12, 0, 30][n % 6]
        sizes[stem] = count
        jd = root / "jds" / f"{stem}.md"
        jd.write_text(f"# {title}\n\nLocation: Gurugram, Haryana.\n\n"
                      + "## Responsibilities\n" + "\n".join(f"- Duty {k}" for k in range(12))
                      + "\n\n## Requirements\n" + "\n".join(f"- {t}" for _r, t, *_ in REQUIREMENTS)
                      + "\n", encoding="utf-8")
        meta = dict(job_location="IN", icims_req_id=f"REQ-{1000 + n}",
                    created_by=ACTOR if n % 3 else "meera.test@example.com",
                    created_at=_iso(t0 + timedelta(days=n)), status="active")
        if n % 4 == 0:
            meta["hm"] = {"email": "gerard.test@example.com", "name": "Gerard Branaola", "status": None}
        if n % 2 == 0:
            meta["work_location"] = {"place": "Gurugram", "radius_km": 50, "max_km": None}
        save_req_meta(jd, **meta)
        (root / "jds" / f"{stem}.rubric.v1.yaml").write_text(_rubric_yaml(stem, title), encoding="utf-8")
        # An approved checklist with no critic-flagged rows — the state every screened
        # requisition is in — so the dashboard computes real stages, not "Draft checklist".
        record_publish(jd, version=1, flagged_ids=[])

        resumes = root / "data" / "roles" / stem / "resumes"
        resumes.mkdir(parents=True)
        if not count:
            continue
        out = root / "output" / stem
        (out / "candidates").mkdir(parents=True)
        (out / "private").mkdir()
        cids: list[str] = []
        for i in range(count):
            f = resumes / f"{i:03d} - Candidate {i}.pdf"
            data = f"%PDF-1.4 synthetic {stem} {i}".encode()
            f.write_bytes(data)
            cid = candidate_id_for(data)   # the engine's own id: file-bytes hash
            cids.append(cid)
            (out / "candidates" / f"{cid}.json").write_text(
                json.dumps(_record(cid, i, title)), encoding="utf-8")
        (out / "private" / "candidate_map.json").write_text(
            json.dumps({cid: str(resumes / f"{i:03d} - Candidate {i}.pdf") for i, cid in enumerate(cids)}),
            encoding="utf-8")
        eligible = [c for i, c in enumerate(cids) if i % 9 != 0]
        (out / "run_meta.json").write_text(json.dumps({
            "run_id": f"run-{stem}-2", "role_title": title, "jurisdiction": "IN", "policy_pack": "IN",
            "started_utc": _iso(t0 + timedelta(days=n, hours=2)), "duration_seconds": 860,
            "counts": {"scored": count, "gate_passers": len(eligible), "errors": 0},
            "bands": {"Strong": count // 10, "Possible": count // 2, "Not a fit": count - count // 10 - count // 2},
            "versions": {"rubric": f"{stem}.rubric.v1.yaml (v1)", "scoring_model": scoring_model_stamp(),
                         "policy_files": policy_files, "preferences": {"applied": []}},
            "obligations": [],
        }), encoding="utf-8")

    # Decisions: append-only, most on the big req, spread over TWO runs with
    # re-decisions — the log a pilot accumulates (later lines win).
    _write_decisions(root, sizes, decisions_total, t0)
    _write_events(root, sizes, events_total, t0)
    return sizes


def _candidate_ids(root: Path, stem: str) -> list[str]:
    return sorted(p.stem for p in (root / "output" / stem / "candidates").glob("*.json"))


def _write_decisions(root: Path, sizes: dict[str, int], total: int, t0: datetime) -> None:
    lines: list[str] = []
    actions = ["agree", "override", "agree", "rerank"]
    bands = ["Strong", "Possible", None, "Not a fit"]
    stems = [s for s, c in sizes.items() if c]
    k = 0
    while len(lines) < total:
        for stem in stems:
            cids = _candidate_ids(root, stem)
            share = max(1, total * sizes[stem] // sum(sizes.values()))
            for j in range(share):
                if len(lines) >= total:
                    break
                cid = cids[(j + k) % len(cids)]
                run = f"run-{stem}-{1 if (j + k) % 5 == 0 else 2}"   # a fifth belong to the OLD run
                lines.append(json.dumps({
                    "candidate_id": cid, "run_id": run, "req_id": stem,
                    "suggestion": "Possible (71)", "human_action": actions[(j + k) % 4],
                    "final_band": bands[(j + k) % 4], "reason": "Move forward" if (j + k) % 2 else "Discuss",
                    "reviewer": ACTOR, "versions": {"rubric": "v1", "scoring_model": "v1 (abc)"},
                    "ts": _iso(t0 + timedelta(days=3, minutes=len(lines))),
                }))
        k += 1
    (root / "output" / "feedback" / "decisions.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_events(root: Path, sizes: dict[str, int], total: int, t0: datetime) -> None:
    lines: list[str] = []
    stems = list(sizes)
    kinds = ["decision", "audit_view", "checklist_publish", "comment", "shortlist_shared",
             "requisition_created", "batch_uploaded", "notify"]
    for stem, count in sizes.items():
        lines.append(json.dumps({"ts": _iso(t0), "kind": "requisition_created", "actor": ACTOR,
                                 "actor_role": "TASpecialist", "req_id": stem,
                                 "summary": "Requisition created", "data": {}}))
        if count:
            for r in (1, 2):
                lines.append(json.dumps({
                    "ts": _iso(t0 + timedelta(days=r, hours=2)), "kind": "screen_completed", "actor": ACTOR,
                    "actor_role": "TASpecialist", "req_id": stem, "summary": f"Screened {count} résumés",
                    "data": {"run_id": f"run-{stem}-{r}", "counts": {"scored": count, "errors": 0},
                             "duration_seconds": 860}}))
    i = 0
    while len(lines) < total:
        stem = stems[i % len(stems)]
        lines.append(json.dumps({"ts": _iso(t0 + timedelta(days=4, minutes=i)), "kind": kinds[i % len(kinds)],
                                 "actor": ACTOR, "actor_role": "TASpecialist", "req_id": stem,
                                 "summary": f"Event {i}", "data": {"to": ACTOR} if kinds[i % len(kinds)] == "notify" else {}}))
        i += 1
    (root / "signals" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- instrumentation ----------------------------------------------------------------


class Counters:
    def __init__(self) -> None:
        self.open = 0
        self.stat = 0
        self.scandir = 0
        self.sql = 0

    @property
    def fs(self) -> int:
        return self.open + self.stat + self.scandir

    def reset(self) -> None:
        self.open = self.stat = self.scandir = self.sql = 0


COUNTERS = Counters()


SITES: dict[str, int] = {}      # "module.function" -> fs ops, when profiling
SQL_SITES: dict[str, int] = {}  # "module.function" -> queries, when profiling
PROFILE = False


def _attribute_to(bucket: dict[str, int], depth: int) -> None:
    """Charge one operation to the nearest caller that is OUR code."""
    f = sys._getframe(depth)
    while f is not None:
        fn = f.f_code.co_filename.replace("\\", "/")
        if "/resume_screener/" in fn and "/site-packages/" not in fn:
            key = f"{fn.rsplit('/resume_screener/', 1)[1]}:{f.f_code.co_name}"
            bucket[key] = bucket.get(key, 0) + 1
            return
        f = f.f_back


def _attribute() -> None:
    """Charge this fs op to the nearest caller that is OUR code (src/resume_screener),
    skipping pathlib/os/json frames — the answer to "who is doing all the stats?"."""
    f = sys._getframe(2)
    while f is not None:
        fn = f.f_code.co_filename.replace("\\", "/")
        if "/resume_screener/" in fn and "/site-packages/" not in fn:
            key = f"{fn.rsplit('/resume_screener/', 1)[1]}:{f.f_code.co_name}"
            SITES[key] = SITES.get(key, 0) + 1
            return
        f = f.f_back


def _install_counters() -> None:
    import io

    real_open, real_stat, real_lstat = builtins.open, os.stat, os.lstat
    real_scandir, real_listdir = os.scandir, os.listdir

    def c_open(*a, **k):
        COUNTERS.open += 1
        if PROFILE:
            _attribute()
        return real_open(*a, **k)

    def c_stat(*a, **k):
        COUNTERS.stat += 1
        if PROFILE:
            _attribute()
        return real_stat(*a, **k)

    def c_lstat(*a, **k):
        COUNTERS.stat += 1
        if PROFILE:
            _attribute()
        return real_lstat(*a, **k)

    def c_scandir(*a, **k):
        COUNTERS.scandir += 1
        if PROFILE:
            _attribute()
        return real_scandir(*a, **k)

    def c_listdir(*a, **k):
        COUNTERS.scandir += 1
        if PROFILE:
            _attribute()
        return real_listdir(*a, **k)

    builtins.open = io.open = c_open
    os.stat, os.lstat, os.scandir, os.listdir = c_stat, c_lstat, c_scandir, c_listdir
    try:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, "before_cursor_execute")
        def _count_sql(*_a, **_k):
            COUNTERS.sql += 1
            # A query is a round trip like any other; "who is doing all the SELECTs?"
            # is the same question as "who is doing all the stats?", and an N+1 is
            # invisible without the answer.
            if PROFILE:
                _attribute_to(SQL_SITES, 2)
    except Exception:  # noqa: BLE001 — SQLAlchemy absent = nothing to count
        pass


@contextmanager
def _measure():
    COUNTERS.reset()
    w0, c0 = time.perf_counter(), time.process_time()
    yield
    COUNTERS.wall_ms = (time.perf_counter() - w0) * 1000          # type: ignore[attr-defined]
    COUNTERS.cpu_ms = (time.process_time() - c0) * 1000           # type: ignore[attr-defined]


# --- the run --------------------------------------------------------------------------


ENDPOINTS = [
    ("me",                "/api/me"),
    ("dashboard",         "/api/roles"),
    ("dash-poll",         "/api/screen/active"),
    ("open-big",          f"/api/roles/{BIG}"),
    ("ws-poll-big",       f"/api/roles/{BIG}/screen/active"),
    ("review-big",        f"/api/roles/{BIG}/review"),
    ("batch-big",         f"/api/roles/{BIG}/batch"),
    ("runs-big",          f"/api/roles/{BIG}/screen/runs"),
    ("preflight-big",     f"/api/roles/{BIG}/screen/preflight"),
    ("review-medium",     f"/api/roles/{MEDIUM}/review"),
    ("notifications",     "/api/notifications"),
    ("bootstrap",         "/api/bootstrap"),
    # The governed-config reads. Added 2026-09-15 because the hosted slow-request
    # census named all three and this table measured none of them: /api/locations sits
    # on the dashboard, Overview AND the New-requisition form (median 849 ms hosted,
    # max 2.5 s), and /api/scoring-model was the single worst non-model request in the
    # log at 19.6 s. An endpoint no benchmark calls is an endpoint nobody optimises.
    ("locations",         "/api/locations"),
    ("jurisdictions",     "/api/jurisdictions"),
    ("scoring-model",     "/api/scoring-model"),
]


# Every domain UAT runs on Postgres. Naming them here (rather than "set the ones I
# remember") is the point: a benchmark that silently exercises the FILE branch of a
# hosted-DB deployment measures code nobody runs — which is exactly why the N+1s in
# the DB branches survived the 2026-09-05 pass.
DB_BACKENDS = (
    "FEEDBACK_BACKEND", "GOVERNANCE_BACKEND", "RUN_INDEX_BACKEND", "JOBS_BACKEND",
    "REGISTRY_BACKEND", "POLICY_BACKEND", "RUBRIC_BACKEND", "REQUISITIONS_BACKEND",
    "PREFS_BACKEND", "EVENTS_BACKEND", "HM_TAKES_BACKEND", "FLAGS_BACKEND",
)


def _prepare_database(url: str) -> None:
    """Migrate a THROWAWAY database and load the synthetic root into it.

    The caller supplies the URL; nothing here creates or drops a server. Point it at
    a scratch container (the repo's own resume-screener-db publishes no host port,
    and a pilot database must never be a benchmark target)."""
    os.environ["DATABASE_URL"] = url
    from alembic import command
    from alembic.config import Config

    # A FRESH schema every run. importers skip a non-empty table, so re-using a
    # database silently measures whatever the LAST run imported: a 200-requisition
    # root was measured against 25 imported rows, and the dashboard looked flat in
    # the number of requisitions because it was.
    from sqlalchemy import create_engine, text

    probe = create_engine(url)
    with probe.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    probe.dispose()

    ini = REPO / "alembic.ini"
    cfg = Config(str(ini))
    # Both are relative in alembic.ini and we have chdir'd into the synthetic root.
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    from resume_screener import rbac
    from resume_screener.db.importers import import_all
    from resume_screener.db.seed import seed_region_framework
    from resume_screener.db.session import session_scope

    with session_scope() as session:
        seed_region_framework(session, dev_user=rbac.DEFAULT_USER, dev_role="SuperAdmin")
    with session_scope() as session:
        import_all(session)
    for name in DB_BACKENDS:
        os.environ[name] = "db"


def run(runs: int, json_out: str | None, keep: bool, profile: str | None = None,
        database_url: str | None = None, reqs: int = 25) -> int:
    global PROFILE
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    except Exception:  # noqa: BLE001
        pass
    os.environ.update({"DATABASE_URL": database_url or "sqlite://", "APP_MODE": "dev",
                       "DEV_USER": "arjun.test@example.com",
                       "DEV_ROLE": "SuperAdmin", "BATCH_PREP_INLINE": "1"})
    for name in DB_BACKENDS:
        os.environ.pop(name, None)
    os.environ.pop("DATA_ROOT", None)
    root = Path(tempfile.mkdtemp(prefix="screener-perf-"))
    cwd = os.getcwd()
    os.chdir(root)
    try:
        t = time.perf_counter()
        sizes = build_root(root, reqs=reqs)
        print(f"root: {root}  ({len(sizes)} reqs, {sum(sizes.values())} candidates, "
              f"built in {time.perf_counter() - t:.1f}s)")
        if database_url:
            t = time.perf_counter()
            _prepare_database(database_url)
            print(f"database: migrated + imported in {time.perf_counter() - t:.1f}s "
                  f"({len(DB_BACKENDS)} backends on db)")
        _install_counters()

        from fastapi.testclient import TestClient
        from resume_screener.api.app import app

        client = TestClient(app)
        results = []
        for label, path in ENDPOINTS:
            r = client.get(path)
            if r.status_code != 200:
                print(f"  !! {label} {path} -> {r.status_code}: {r.text[:160]}")
                continue
            if profile and label in profile.split(","):
                SITES.clear(); SQL_SITES.clear(); PROFILE = True
                client.get(path)
                PROFILE = False
                print()
                print(f"--- fs ops by code site for {label} ({sum(SITES.values())} total) ---")
                for site, n in sorted(SITES.items(), key=lambda kv: -kv[1])[:22]:
                    print(f"  {n:6d}  {site}")
                if SQL_SITES:
                    print(f"--- QUERIES by code site for {label} "
                          f"({sum(SQL_SITES.values())} total) ---")
                    for site, n in sorted(SQL_SITES.items(), key=lambda kv: -kv[1])[:22]:
                        print(f"  {n:6d}  {site}")
            walls, cpus, fss, sqls = [], [], [], []
            for _ in range(runs):
                with _measure():
                    client.get(path)
                walls.append(COUNTERS.wall_ms); cpus.append(COUNTERS.cpu_ms)   # type: ignore[attr-defined]
                fss.append(COUNTERS.fs); sqls.append(COUNTERS.sql)
            results.append({"label": label, "path": path, "wall_ms": statistics.median(walls),
                            "cpu_ms": statistics.median(cpus), "fs_ops": int(statistics.median(fss)),
                            "open": COUNTERS.open, "stat": COUNTERS.stat, "scandir": COUNTERS.scandir,
                            "sql": int(statistics.median(sqls)), "bytes": len(r.content)})
        print(f"\n{'endpoint':16} {'wall ms':>8} {'cpu ms':>8} {'fs ops':>7} {'open':>5} {'stat':>5} {'scan':>5} {'sql':>4} {'kB':>6}")
        for x in results:
            print(f"{x['label']:16} {x['wall_ms']:8.1f} {x['cpu_ms']:8.1f} {x['fs_ops']:7d} {x['open']:5d} "
                  f"{x['stat']:5d} {x['scandir']:5d} {x['sql']:4d} {x['bytes']/1024:6.0f}")
        est = sum(x["fs_ops"] for x in results if x["label"] in ("dashboard", "open-big", "review-big"))
        print(f"\nhosted estimate: dashboard + open + review = {est} fs ops "
              f"~ {est * 5 / 1000:.1f}s of SMB round trips at 5 ms each (before any compute)")
        if json_out:
            Path(cwd, json_out).write_text(json.dumps(results, indent=1), encoding="utf-8")
            print(f"wrote {json_out}")
        return 0
    finally:
        os.chdir(cwd)
        if keep:
            print(f"kept {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--keep", action="store_true", help="leave the synthetic root on disk")
    ap.add_argument("--profile", metavar="LABEL[,LABEL]",
                    help="attribute these endpoints' fs ops to code sites")
    ap.add_argument("--reqs", type=int, default=25,
                    help="how many requisitions in the synthetic root (scale test)")
    ap.add_argument("--database-url", metavar="URL",
                    help="run every domain on this database, as UAT does. THROWAWAY "
                         "databases only — it migrates and imports into whatever you "
                         "point it at.")
    a = ap.parse_args()
    raise SystemExit(run(a.runs, a.json_out, a.keep, a.profile, a.database_url, a.reqs))
