"""P47: the Data page — the storage map + the two deletion actions.

Guardrail §6.12: every test runs on a SYNTHETIC tmp_path tree — never the real
data/, output/ or .cache/. Covers cache empty+recreate, feedback/ surviving a
candidate wipe, per-role batches cleared, dot-file placeholders kept, stray
.lock sidecars swept, the typed-confirm 400s, the event + governance audit
double-record, and the data_manage RBAC guard.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed_candidate_tree(root: Path) -> None:
    """A representative disk state: cache, legacy résumés, a per-role batch, output
    with results + the anonymous feedback log, and stray .lock sidecars."""
    (root / ".cache").mkdir()
    (root / ".cache" / "abc.json").write_text("{}", encoding="utf-8")

    resumes = root / "data" / "resumes"
    resumes.mkdir(parents=True)
    (resumes / "old.pdf").write_bytes(b"resume")
    (resumes / ".gitkeep").write_text("", encoding="utf-8")  # placeholder must survive

    batch = root / "data" / "roles" / "finance-analyst" / "resumes"
    batch.mkdir(parents=True)
    (batch / "anita.pdf").write_bytes(b"resume")

    out = root / "output" / "finance-analyst"
    (out / "candidates").mkdir(parents=True)
    (out / "candidates" / "c1.json").write_text("{}", encoding="utf-8")
    (out / "private").mkdir()
    (out / "private" / "candidate_map.json").write_text("{}", encoding="utf-8")

    feedback = root / "output" / "feedback"
    feedback.mkdir(parents=True)
    (feedback / "decisions.jsonl").write_text("{}\n", encoding="utf-8")
    (feedback / "decisions.jsonl.lock").write_text("", encoding="utf-8")  # stray sidecar


# --- engine ------------------------------------------------------------------------


def test_delete_cache_empties_and_recreates(workspace):
    from resume_screener import datamgmt

    _seed_candidate_tree(workspace)
    datamgmt.delete_cache(".cache")

    cache = workspace / ".cache"
    assert cache.is_dir() and list(cache.iterdir()) == []  # recreated but empty


def test_delete_candidate_data_keeps_feedback_and_placeholders(workspace):
    from resume_screener import datamgmt

    _seed_candidate_tree(workspace)
    datamgmt.delete_candidate_data("data/resumes", "data/roles")

    # legacy résumés gone, the .gitkeep placeholder kept
    assert not (workspace / "data" / "resumes" / "old.pdf").exists()
    assert (workspace / "data" / "resumes" / ".gitkeep").exists()
    # per-role batch cleared
    assert not (workspace / "data" / "roles" / "finance-analyst" / "resumes" / "anita.pdf").exists()
    # screening results + private map gone
    assert not (workspace / "output" / "finance-analyst").exists()
    # the anonymous decision log survives — it is the audit trail
    assert (workspace / "output" / "feedback" / "decisions.jsonl").exists()
    # stray lock sidecar swept
    assert not (workspace / "output" / "feedback" / "decisions.jsonl.lock").exists()


def test_storage_map_reports_relative_paths_and_sizes(workspace):
    from resume_screener import datamgmt
    from resume_screener.config import load_app_config

    _seed_candidate_tree(workspace)
    rows = datamgmt.storage_map(load_app_config())
    by_kind = {r["kind"]: r for r in rows}

    secrets = next(r for r in rows if r["kind"].startswith("Secrets"))
    assert secrets["path"] == "Azure Key Vault" and secrets["size"] == "—"
    # a real folder is named relative to the data root (stated once, as data_root —
    # 2026-10-16, pen-test RS-12), with a non-dash size
    cache = by_kind["Cache"]
    assert not Path(cache["path"]).is_absolute()
    assert all(str(workspace.resolve()) not in r["path"] for r in rows)
    assert cache["size"] != "—"
    assert datamgmt.storage_root() == str(workspace.resolve())


# --- API ---------------------------------------------------------------------------


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def test_summary_and_deletes_via_api(client, workspace):
    from tests.test_sso_identity import _as
    from resume_screener.events import load_events
    from resume_screener.governance import load_audit

    _seed_candidate_tree(workspace)

    # 2026-07-19: destructive data deletion moved to the Super Admin —
    # the HR Head gets a plain 403 on every data route.
    assert client.get("/api/data/summary", headers=_as("HRHead")).status_code == 403
    summary = client.get("/api/data/summary", headers=_as("SuperAdmin"))
    assert summary.status_code == 200
    assert summary.json()["data_root"] == str(workspace.resolve())
    assert any(r["kind"] == "Cache" for r in summary.json()["rows"])

    # wrong phrase → 400, nothing deleted
    bad = client.post("/api/data/delete-cache", headers=_as("SuperAdmin"), json={"confirm": "nope"})
    assert bad.status_code == 400
    assert (workspace / ".cache" / "abc.json").exists()

    ok = client.post("/api/data/delete-cache", headers=_as("SuperAdmin"), json={"confirm": "delete cache"})
    assert ok.status_code == 200 and "rebuilds it" in ok.json()["message"]
    assert list((workspace / ".cache").iterdir()) == []

    ok2 = client.post("/api/data/delete-candidates", headers=_as("SuperAdmin"),
                      json={"confirm": "delete all candidate data"})
    assert ok2.status_code == 200
    assert not (workspace / "output" / "finance-analyst").exists()
    assert (workspace / "output" / "feedback" / "decisions.jsonl").exists()

    # both actions doubly recorded
    assert len(load_events(kinds=["data_deleted"])) == 2
    assert [e for e in load_audit() if e.get("action") == "data_delete"]


def test_data_routes_are_rbac_guarded(client):
    from tests.test_sso_identity import _as

    # a TA Specialist (no data_manage) is refused on all three
    assert client.get("/api/data/summary", headers=_as("TASpecialist")).status_code == 403
    assert client.post("/api/data/delete-cache", headers=_as("TASpecialist"),
                       json={"confirm": "delete cache"}).status_code == 403
    assert client.post("/api/data/delete-candidates", headers=_as("TASpecialist"),
                       json={"confirm": "delete all candidate data"}).status_code == 403


def test_candidate_sidecars_die_with_the_candidates(tmp_path):
    """The split manifest (candidate names) and the contact index (emails/phones)
    describe candidates, so candidate deletion must remove them even though
    _clear_dir keeps dot-files."""
    from resume_screener import batch_prep, datamgmt, resume_split

    batch = tmp_path / "roles" / "finance-analyst" / "resumes"
    batch.mkdir(parents=True)
    (batch / "01 - Someone.pdf").write_bytes(b"%PDF-1.4 x")
    (batch / resume_split.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    (batch / batch_prep.INDEX_NAME).write_text("{}", encoding="utf-8")
    (batch / batch_prep.STATE_NAME).write_text("{}", encoding="utf-8")
    (batch / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "legacy").mkdir()

    datamgmt.delete_candidate_data(tmp_path / "legacy", tmp_path / "roles",
                                   tmp_path / "output")

    assert not (batch / "01 - Someone.pdf").exists()
    assert not (batch / resume_split.MANIFEST_NAME).exists()
    assert not (batch / batch_prep.INDEX_NAME).exists()
    assert not (batch / batch_prep.STATE_NAME).exists()
    assert (batch / ".gitkeep").exists()  # placeholders stay
