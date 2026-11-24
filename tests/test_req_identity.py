"""A requisition's identity is its ID — never a file path (2026-08-26).

THE PILOT INCIDENT. Two requisitions pointed at the same `jds/*.md`. `screener run`
resolved which one to screen by matching the JD PATH and taking the first match, so
a run launched for the 235-candidate requisition screened the OTHER requisition's 6
résumés and wrote the results into the first one's output folder. Every candidate the
recruiter had just uploaded was silently skipped; the tab said "Screened 0 of 6".

Two rails, both tested here:
  * the API always passes --req, so the run screens the requisition it was asked to;
  * a bare --jd that matches SEVERAL requisitions is refused, loudly, instead of
    silently picking one.

Plus the state-scoping rails: a deleted requisition's screening state dies with it,
so recreating one with the same id never inherits a phantom run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "jds").mkdir()
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _role(workspace, req_id: str, jd_stem: str, resumes: int) -> None:
    """A requisition whose sidecar points at `jd_stem`, with `resumes` files in ITS
    own batch folder (keyed by req_id, which is what the ATS port resolves)."""
    jd = workspace / "jds" / f"{jd_stem}.md"
    if not jd.exists():
        jd.write_text(f"# {req_id}\nOwns invoice processing.\n", encoding="utf-8")
    batch = workspace / "data" / "roles" / req_id / "resumes"
    batch.mkdir(parents=True, exist_ok=True)
    for i in range(resumes):
        (batch / f"cand-{i:02d}.pdf").write_bytes(f"%PDF-1.4 {req_id} {i}".encode())


def _ats(workspace):
    from resume_screener.ats.local_folder import LocalFolderATS

    return LocalFolderATS(jd_dir=workspace / "jds",
                          resumes_dir=workspace / "data" / "resumes",
                          default_job_location="IN",
                          roles_dir=workspace / "data" / "roles")


def test_two_requisitions_can_share_a_jd_and_keep_separate_batches(workspace):
    """The precondition of the incident, pinned: sharing a JD is possible, and each
    requisition's candidates are its own."""
    from resume_screener.models import Requisition

    _role(workspace, "invoice-processing", "invoice-processing", resumes=6)
    _role(workspace, "invoice-processing-2", "invoice-processing", resumes=235)
    ats = _ats(workspace)

    small = Requisition(req_id="invoice-processing", role_title="A",
                        jd_ref="jds/invoice-processing.md")
    big = Requisition(req_id="invoice-processing-2", role_title="A",
                      jd_ref="jds/invoice-processing.md")
    assert len(ats.fetch_candidates(small)) == 6
    assert len(ats.fetch_candidates(big)) == 235


def test_run_resolves_by_id_not_by_jd_path(workspace):
    """--req decides. Without it the run screened whichever requisition came first
    out of the ATS port, which is how the 235-candidate req screened 6 résumés."""
    from resume_screener.cli import resolve_requisition
    from resume_screener.models import Requisition

    rows = [
        Requisition(req_id="invoice-processing", role_title="A",
                    jd_ref="jds/invoice-processing.md"),
        Requisition(req_id="invoice-processing-2", role_title="A",
                    jd_ref="jds/invoice-processing.md"),
    ]
    chosen = resolve_requisition(rows, Path("jds/invoice-processing.md"),
                                 "invoice-processing-2")
    assert chosen.req_id == "invoice-processing-2"
    # ...and the first one is still reachable BY ID, not by accident of order.
    assert resolve_requisition(rows, Path("jds/invoice-processing.md"),
                               "invoice-processing").req_id == "invoice-processing"


def test_a_shared_jd_without_an_id_is_refused_not_guessed(workspace):
    """The safety net for CLI users: an ambiguous --jd names the problem and both
    candidates, rather than silently screening one of them."""
    from resume_screener.cli import resolve_requisition
    from resume_screener.models import Requisition

    rows = [
        Requisition(req_id="invoice-processing", role_title="A",
                    jd_ref="jds/invoice-processing.md"),
        Requisition(req_id="invoice-processing-2", role_title="A",
                    jd_ref="jds/invoice-processing.md"),
    ]
    with pytest.raises(ValueError) as err:
        resolve_requisition(rows, Path("jds/invoice-processing.md"))
    message = str(err.value)
    assert "share" in message and "--req" in message
    assert "invoice-processing-2" in message and "invoice-processing," in message

    # An unambiguous JD still resolves without an id (the ordinary CLI case).
    single = [rows[0]]
    assert resolve_requisition(single, Path("jds/invoice-processing.md")).req_id == "invoice-processing"
    # An unknown id is an error, never a silent fallback to the path match.
    with pytest.raises(ValueError):
        resolve_requisition(rows, Path("jds/invoice-processing.md"), "no-such-req")


def test_the_api_always_passes_the_requisition_id(monkeypatch):
    """The route must never rely on the JD path being unique."""
    from resume_screener.api import jobs

    captured: dict = {}
    monkeypatch.setattr(jobs, "_batch_names", lambda req_id: [])
    monkeypatch.setattr(jobs, "_prior_run_state", lambda req_id: {})
    monkeypatch.setattr(jobs.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: None})())

    def spy(job_id, req_id, cmd, store, actor=""):
        captured["cmd"] = cmd

    monkeypatch.setattr(jobs, "_run", spy)
    # Thread is stubbed, so grab the cmd from the args it would have been given.
    real_thread = jobs.threading.Thread

    def capture_thread(target, args, daemon):
        captured["cmd"] = args[2]
        return type("T", (), {"start": lambda self: None})()

    monkeypatch.setattr(jobs.threading, "Thread", capture_thread)
    jobs.start_screening("invoice-processing-2", "jds/shared.md")

    cmd = captured["cmd"]
    assert "--req" in cmd and cmd[cmd.index("--req") + 1] == "invoice-processing-2"


# --- state dies with the requisition ---------------------------------------------------


def test_deleting_a_requisition_forgets_its_screening_state():
    """A job record that outlives its requisition made a recreated one look locked:
    screen_lock still saw a running job, batch edits 409'd, and the tab showed a run
    that no longer existed. That is the "screening state is stuck" report."""
    from resume_screener.api import jobs

    store = jobs._MemoryStore()
    store.create("job-1", "invoice-processing-2")
    store.save("job-1", "invoice-processing-2", {"status": "running"})
    store.create("job-2", "another-req")
    store.save("job-2", "another-req", {"status": "running"})

    assert store.read_active("invoice-processing-2") is not None
    assert store.forget("invoice-processing-2") == 1
    assert store.read_active("invoice-processing-2") is None
    assert store.read_latest("invoice-processing-2") is None
    # A neighbour's run is untouched — deletion is scoped to one requisition.
    assert store.read_active("another-req") is not None


def test_run_history_ignores_a_previous_requisition_that_reused_the_id(workspace):
    """Deleting a requisition and creating another with the same id showed the DEAD
    one's screening history in the new, empty workspace."""
    import json

    from resume_screener.api import service
    from resume_screener.ats.local_folder import save_req_meta
    from resume_screener.models import Requisition

    signals = workspace / "signals"
    signals.mkdir(exist_ok=True)
    jd = workspace / "jds" / "invoice-processing.md"
    jd.write_text("# A\nOwns invoices.\n", encoding="utf-8")

    # Explicit timestamps: the dead requisition's run, then this one's creation.
    rows = [
        {"ts": "2026-08-01T10:00:00+00:00", "kind": "screen_completed",
         "req_id": "invoice-processing", "actor": "old@example.com",
         "summary": "old run", "data": {"run_id": "old"}},
        {"ts": "2026-08-20T10:00:00+00:00", "kind": "screen_completed",
         "req_id": "invoice-processing", "actor": "new@example.com",
         "summary": "new run", "data": {"run_id": "new"}},
    ]
    (signals / "events.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    save_req_meta(jd, created_at="2026-08-15T00:00:00+00:00", icims_req_id="IN-1")

    req = Requisition(req_id="invoice-processing", role_title="A", jd_ref=str(jd))
    summaries = [r.get("summary") for r in service.screen_runs(req)["runs"]]
    assert "new run" in summaries
    assert "old run" not in summaries, "the previous requisition's history leaked in"


def test_run_history_survives_a_requisition_with_no_created_at(workspace):
    """Legacy/pre-P29 requisitions have no created_at; their history must still show
    rather than being filtered away by a scoping rule they predate."""
    import json

    from resume_screener.api import service
    from resume_screener.models import Requisition

    signals = workspace / "signals"
    signals.mkdir(exist_ok=True)
    jd = workspace / "jds" / "legacy.md"
    jd.write_text("# Legacy\n", encoding="utf-8")
    (signals / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-08-01T10:00:00+00:00", "kind": "screen_completed",
         "req_id": "legacy", "actor": "a@example.com", "summary": "a run",
         "data": {"run_id": "r"}}) + "\n", encoding="utf-8")

    req = Requisition(req_id="legacy", role_title="Legacy", jd_ref=str(jd))
    assert [r.get("summary") for r in service.screen_runs(req)["runs"]] == ["a run"]


# --- sidecar fields the DB backend does not carry --------------------------------------


def test_every_sidecar_only_field_is_hydrated(workspace):
    """The DB requisitions table carries four columns; anything else on the contract
    lives in the sidecar and MUST be overlaid, or it reads as None on every
    DB-backed deployment.

    This has bitten twice: work_location (2026-08-25) and then icims_req_id
    (2026-08-26), which made every requisition show a blank Req ID even though
    creation requires one. Asserting the registry COVERS the gap stops a third."""
    from resume_screener.ats.local_folder import SIDECAR_ONLY_FIELDS
    from resume_screener.models import Requisition

    # What db/repositories/requisitions.py populates from its table.
    from_db = {"req_id", "role_title", "jd_ref", "job_location"}
    uncovered = set(Requisition.model_fields) - from_db - set(SIDECAR_ONLY_FIELDS)
    assert not uncovered, f"sidecar-only field(s) with no overlay: {sorted(uncovered)}"


def test_hydration_restores_the_icims_id_and_place(workspace):
    """A DB-sourced row (no iCIMS id, no work location) comes back with both."""
    from resume_screener.ats.local_folder import hydrate_sidecar_only_fields, save_req_meta
    from resume_screener.models import Requisition

    jd = workspace / "jds" / "invoice-processing.md"
    jd.write_text("# A\n", encoding="utf-8")
    save_req_meta(jd, icims_req_id="IN-24601",
                  work_location={"place": "Gurgaon", "radius_km": 25, "max_km": 100})

    bare = Requisition(req_id="invoice-processing", role_title="A", jd_ref=str(jd))
    assert bare.icims_req_id is None  # what the DB repo hands over

    (hydrated,) = hydrate_sidecar_only_fields([bare])
    assert hydrated.icims_req_id == "IN-24601"
    assert hydrated.work_location is not None
    assert hydrated.work_location.place == "Gurgaon"


def test_the_careers_bridge_reports_the_city_canonicalised():
    """The city is captured at intake from the posting's structured location, because
    the stored JD body does not keep it (the boilerplate stripper removes it — a real
    requisition's JD says only "Legal Entity: … (India) Pvt Ltd").

    Canonicalised so Gurgaon and Gurugram can never look like two different sites."""
    from resume_screener.careers_lookup import _work_place

    assert _work_place("Gurgaon, Haryana, India") == "Gurugram"
    assert _work_place("Gurugram, Haryana, India") == "Gurugram"
    assert _work_place("Bangalore, Karnataka, India") == "Bengaluru"
    # Unknown to the gazetteer: keep the locality the posting stated, not the country.
    assert _work_place("Nowhereville, Someplace, India") == "Nowhereville"
    assert _work_place(None) is None
    assert _work_place("") is None


def test_db_rows_point_at_their_own_jd_not_the_shared_document(workspace):
    """`jd_document` is keyed by CONTENT HASH, so two openings for the same role
    (identical JD text — completely ordinary) share one row and its `source_ref` is
    whichever requisition was written last.

    Everything per-requisition is derived from jd_ref: the rubric AND the .req.json
    sidecar holding the iCIMS id, work location, lifecycle and created_at. Sharing it
    meant two requisitions read and wrote each other's metadata."""
    from resume_screener.ats.local_folder import save_req_meta
    from resume_screener.models import Requisition

    (workspace / "jds" / "invoice-processing.md").write_text("# A\nSame text.\n", encoding="utf-8")
    (workspace / "jds" / "invoice-processing-2.md").write_text("# A\nSame text.\n", encoding="utf-8")
    # Distinct sidecars — the whole point of keeping them apart.
    save_req_meta(workspace / "jds" / "invoice-processing.md", icims_req_id="IN-100")
    save_req_meta(workspace / "jds" / "invoice-processing-2.md", icims_req_id="IN-200")

    ats = _ats(workspace)
    # What the DB hands over: BOTH rows carrying the shared document's source_ref.
    shared = "jds/invoice-processing-2.md"
    rows = [Requisition(req_id="invoice-processing", role_title="A", jd_ref=shared),
            Requisition(req_id="invoice-processing-2", role_title="A", jd_ref=shared)]

    fixed = [ats._own_jd(r) for r in rows]
    assert Path(fixed[0].jd_ref).stem == "invoice-processing"
    assert Path(fixed[1].jd_ref).stem == "invoice-processing-2"

    # …so each reads ITS OWN iCIMS id rather than its neighbour's.
    from resume_screener.ats.local_folder import hydrate_sidecar_only_fields
    hydrated = hydrate_sidecar_only_fields(fixed)
    assert [h.icims_req_id for h in hydrated] == ["IN-100", "IN-200"]


def test_own_jd_falls_back_when_there_is_no_matching_file(workspace):
    """A row whose JD genuinely lives elsewhere keeps the document's reference."""
    from resume_screener.models import Requisition

    ats = _ats(workspace)
    row = Requisition(req_id="no-file-here", role_title="A", jd_ref="jds/elsewhere.md")
    assert ats._own_jd(row).jd_ref == "jds/elsewhere.md"
