"""B7: the file-state-keyed perf caches — parsed config, résumé content hashes,
and the decisions log — collapse the dashboard's repeated re-reads to one read
per file version, and re-read correctly when a file actually changes.

Offline: tmp_path workspace; each cache is keyed on a RESOLVED path + (size,
mtime_ns), so per-test chdir isolation holds.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_caches():
    from resume_screener import config, feedback
    from resume_screener.ats import local_folder

    config._CONFIG_CACHE.clear()
    feedback._DECISIONS_CACHE.clear()
    local_folder._HASH_CACHE.clear()  # one résumé-hash cache, owned by local_folder
    yield


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    import shutil

    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- config cache ---------------------------------------------------------------------


def test_config_cache_reparses_only_on_change(workspace, monkeypatch):
    from resume_screener import config

    calls = {"n": 0}
    real = config.yaml.safe_load

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(config.yaml, "safe_load", counting)
    config.load_app_config()
    config.load_app_config()
    config.load_app_config()
    assert calls["n"] == 1  # parsed once, then served from the cache

    # editing the file re-parses
    p = Path("config.yaml")
    p.write_text(p.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    config.load_app_config()
    assert calls["n"] == 2


def test_config_cache_returns_independent_copies(workspace):
    from resume_screener import config

    a = config.load_app_config()
    a.runtime.concurrency = 999
    b = config.load_app_config()
    assert b.runtime.concurrency != 999  # a caller's mutation never poisons the cache


def test_config_cache_isolates_two_workspaces(tmp_path, monkeypatch):
    import shutil

    from resume_screener import config

    for name, conc in (("a", 2), ("b", 8)):
        ws = tmp_path / name
        ws.mkdir()
        shutil.copyfile(REPO_ROOT / "config.yaml", ws / "config.yaml")
        text = (ws / "config.yaml").read_text(encoding="utf-8")
        (ws / "config.yaml").write_text(
            text.replace("concurrency: 4", f"concurrency: {conc}"), encoding="utf-8")
    monkeypatch.chdir(tmp_path / "a")
    assert config.load_app_config().runtime.concurrency == 2
    monkeypatch.chdir(tmp_path / "b")
    assert config.load_app_config().runtime.concurrency == 8  # resolved-path keys keep them apart


# --- résumé hash cache ----------------------------------------------------------------


def test_resume_hash_cache_skips_re_hashing(workspace, monkeypatch):
    """service.cached_candidate_id delegates to the ONE cache in local_folder, so
    a résumé hashed for an upload is not hashed again for the dashboard."""
    from resume_screener.api import service
    from resume_screener.ats import local_folder

    f = Path("a.pdf")
    f.write_bytes(b"%PDF-1.4 candidate one")

    calls = {"n": 0}
    real = local_folder.candidate_id_for

    def counting(data):
        calls["n"] += 1
        return real(data)

    monkeypatch.setattr(local_folder, "candidate_id_for", counting)
    first = service.cached_candidate_id(f)
    second = service.cached_candidate_id(f)
    assert first == second
    assert calls["n"] == 1  # bytes hashed once

    # rewriting the file (new bytes) re-hashes
    time.sleep(0.01)
    f.write_bytes(b"%PDF-1.4 candidate one EDITED")
    third = service.cached_candidate_id(f)
    assert third != first
    assert calls["n"] == 2


# --- decisions cache ------------------------------------------------------------------


def test_decisions_cache_reparses_only_on_append(workspace, monkeypatch):
    import json

    from resume_screener import feedback

    path = Path("output/feedback/decisions.jsonl")
    path.parent.mkdir(parents=True)
    line = json.dumps({
        "candidate_id": "c1", "run_id": "run-1", "suggestion": "Strong (80)",
        "human_action": "agree", "final_band": "Strong", "reason": "ok",
        "reviewer": "ta@example.com", "versions": {}, "ts": "2026-07-14T09:00:00+00:00",
    })
    path.write_text(line + "\n", encoding="utf-8")

    calls = {"n": 0}
    real = feedback.Decision.model_validate

    def counting(v):
        calls["n"] += 1
        return real(v)

    monkeypatch.setattr(feedback.Decision, "model_validate", counting)
    assert len(feedback.load_decisions()) == 1
    assert len(feedback.load_decisions()) == 1
    assert calls["n"] == 1  # parsed once across both reads

    time.sleep(0.01)
    with path.open("a", encoding="utf-8") as h:
        h.write(line.replace("c1", "c2") + "\n")
    assert len(feedback.load_decisions()) == 2
    assert calls["n"] == 3  # re-parsed both lines after the append
