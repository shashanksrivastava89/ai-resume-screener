"""A restart must not mean re-reading every résumé on the share.

A candidate's id is the content hash of its file, memoised in a dict that dies with
the process. The first dashboard after every deploy therefore re-hashed the whole
corpus: measured cold on the pilot's data, 1,326 hashes and 1,489 whole-file reads
for one /api/roles, up to 41 s hosted (2026-09-10). batch_prep already writes those
hashes to .contact_index.json for its own de-duplication, so the cache is seeded from
there — but ONLY where the file has not moved since.
"""
from __future__ import annotations

import json

import pytest

from resume_screener.api import service
from resume_screener.ats import local_folder


@pytest.fixture()
def batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "resumes"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF-1.4 alpha")
    (folder / "b.pdf").write_bytes(b"%PDF-1.4 beta")
    local_folder._HASH_CACHE.clear()
    return folder


def _index(folder, **overrides):
    files = {}
    for path in sorted(folder.glob("*.pdf")):
        st = path.stat()
        files[path.name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                            "hash": f"idx-{path.stem}", "tokens": [], "multi": False,
                            **overrides}
    (folder / ".contact_index.json").write_text(
        json.dumps({"schema": 1, "files": files, "sources": {}}), encoding="utf-8")


def test_the_index_saves_the_read(batch, monkeypatch):
    _index(batch)
    reads: list[str] = []
    real = local_folder.candidate_id_for
    monkeypatch.setattr(local_folder, "candidate_id_for",
                        lambda data: (reads.append("read"), real(data))[1])

    service._seed_hashes_from_index(batch)
    assert local_folder.cached_candidate_id(batch / "a.pdf") == "idx-a"
    assert reads == [], "a file the index already described was read anyway"


def test_a_file_that_changed_is_hashed_for_real(batch):
    """The seed is keyed on (size, mtime) like every other cache here, so a stale
    entry cannot outlive the bytes it describes."""
    _index(batch)
    (batch / "a.pdf").write_bytes(b"%PDF-1.4 alpha EDITED and longer")
    local_folder._HASH_CACHE.clear()

    service._seed_hashes_from_index(batch)
    assert local_folder.cached_candidate_id(batch / "a.pdf") != "idx-a"


def test_a_missing_or_broken_index_changes_nothing(batch):
    service._seed_hashes_from_index(batch)          # no index at all
    real = local_folder.cached_candidate_id(batch / "a.pdf")

    (batch / ".contact_index.json").write_text("{ not json", encoding="utf-8")
    local_folder._HASH_CACHE.clear()
    service._seed_hashes_from_index(batch)
    assert local_folder.cached_candidate_id(batch / "a.pdf") == real


def test_the_seed_never_overrides_something_already_known(batch):
    """A digest this process computed itself outranks the file's claim about it."""
    _index(batch)
    truth = local_folder.cached_candidate_id(batch / "a.pdf")
    service._seed_hashes_from_index(batch)
    assert local_folder.cached_candidate_id(batch / "a.pdf") == truth
