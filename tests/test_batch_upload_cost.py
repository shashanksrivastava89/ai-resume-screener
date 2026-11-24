"""Adding résumés must cost O(what you add), not O(what is already there).

The hosted regression this pins: every upload re-hashed every file already in the
batch AND re-extracted the text of every PDF in it (the bulk-print check), so the
same 20-file upload took 26s into an empty batch and 215s into a batch of 200 —
against App Service's hard 230s request timeout, which surfaced as a 504.

The assertions count WORK (files read, pages extracted), not seconds: wall-clock
would be flaky on CI, and the count is the thing that actually grew.

Offline: PDFs are built in-test (never real candidate data, §6.12).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.test_resume_split import make_pdf
from tests.test_sso_identity import _as

from resume_screener import contacts, resume_split
from resume_screener.ats import local_folder

REPO_ROOT = Path(__file__).resolve().parents[1]


def _resume_pdf(n: int, pages: int = 3) -> bytes:
    """An ordinary single-candidate résumé — no Bulk Print TOC."""
    body = [[f"Candidate {n:03d}", "Senior Accountant", f"cand{n:03d}@example.invalid",
             f"+91 98{n:04d} 43210"]]
    body += [["Experience continued", "GL, R2R, Ind AS, GST, SAP FICO"]] * (pages - 1)
    return make_pdf(body)


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
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    # The caches are process-global and other tests share the process; a stale entry
    # for a reused tmp path would mask exactly what this file measures.
    local_folder._HASH_CACHE.clear()
    resume_split._SINGLE_CACHE.clear()
    contacts._TOKEN_CACHE.clear()
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


@pytest.fixture()
def req_id(client):
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Finance Analyst", "jd_text": "Owns treasury accounting.",
        "job_location": "IN", "icims_req_id": "IN-901",
    })
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def _upload(client, req_id, ids: range | list[int]):
    files = [("files", (f"cand-{n:03d}.pdf", _resume_pdf(n), "application/pdf")) for n in ids]
    response = client.post(f"/api/roles/{req_id}/batch/upload",
                           headers=_as("TASpecialist"), files=files)
    assert response.status_code == 200, response.text
    return response.json()


class _Counter:
    """Counts the three whole-batch scans an upload used to redo: hashing every
    file, extracting every PDF for the bulk-print check, and extracting every
    PDF again for contact de-dup."""

    def __init__(self, monkeypatch):
        self.hashed = 0
        self.extracted = 0
        self.contact_reads = 0
        real_hash = local_folder.candidate_id_for
        real_pages = resume_split._read_pages
        real_tokens = contacts.contact_tokens

        def hashed(data):
            self.hashed += 1
            return real_hash(data)

        def extracted(data, limit=None):
            self.extracted += 1
            return real_pages(data, limit)

        def tokens(data, suffix):
            self.contact_reads += 1
            return real_tokens(data, suffix)

        monkeypatch.setattr(local_folder, "candidate_id_for", hashed)
        monkeypatch.setattr(resume_split, "_read_pages", extracted)
        # add_bytes_to_batch and tokens_for_path both resolve this at call time.
        monkeypatch.setattr(contacts, "contact_tokens", tokens)


def test_adding_to_a_big_batch_costs_the_same_as_adding_to_a_small_one(
        client, req_id, monkeypatch):
    _upload(client, req_id, range(40))  # seed a batch

    count = _Counter(monkeypatch)
    batch = _upload(client, req_id, range(40, 42))  # add two more
    assert batch["count"] == 42

    # Two new files: hashed once each, text-extracted once each. The 40 already in
    # the batch must not be touched — that is the whole point.
    assert count.hashed <= 4, f"re-hashed the existing batch ({count.hashed} hashes for 2 files)"
    assert count.extracted <= 4, (
        f"re-extracted the existing batch ({count.extracted} PDF reads for 2 files)")
    assert count.contact_reads <= 4, (
        f"re-read the existing batch for contacts ({count.contact_reads} reads for 2 files)")


def _count_page_extractions(monkeypatch) -> list[int]:
    """Record which page INDEX each text extraction touched.

    Counts pages rather than helper calls (2026-08-26): pages are what actually cost
    the time, and the count catches a page extracted TWICE — which is precisely the
    waste that made a 6-packet bulk upload time out. Spying on a helper's arguments
    could not see that, and broke the moment the helper was replaced."""
    pages: list[int] = []
    real = resume_split._PageText.page

    def spy(self, index):
        # Record only a CACHE MISS — an actual text extraction. Counting calls instead
        # would count cache hits too (all() asks for every page, including ones head()
        # already read) and would report a false double-read.
        miss = index not in self._cache
        result = real(self, index)
        if miss:
            pages.append(index)
        return result

    monkeypatch.setattr(resume_split._PageText, "page", spy)
    return pages


def test_an_ordinary_resume_is_never_read_past_the_toc_pages(monkeypatch):
    """Deciding 'not a bulk print' must not cost a full-document text pass."""
    pages = _count_page_extractions(monkeypatch)
    long_resume = make_pdf([["Candidate", "Senior Accountant"]] * 60)

    segments = resume_split.detect_boundaries(long_resume)

    assert len(segments) == 1                       # unchanged verdict
    assert segments[0].end == 59                    # …and it still spans the real document
    # Only the pages a TOC could live on — not the other 56.
    assert pages == list(range(resume_split._MAX_TOC_PAGES))


def test_a_real_bulk_print_still_reads_the_whole_document(monkeypatch):
    """The saving must not cost correctness: a genuine TOC triggers the full read."""
    from tests.test_resume_split import _bulk_pdf

    pages = _count_page_extractions(monkeypatch)

    segments = resume_split.detect_boundaries(_bulk_pdf())

    assert [s.label for s in segments] == ["Alice Alpha", "Bob Beta", "Carol Gamma"]
    # The whole document IS read for a real Bulk Print...
    assert set(pages) == set(range(5))
    # ...but every page EXACTLY ONCE. Detection reads the head to find the TOC and then
    # the whole document; before the per-document memo the head pages were extracted
    # twice, which was 25s of a 54s packet on the batch that produced a 504.
    assert sorted(pages) == list(range(5)), f"a page was extracted more than once: {pages}"
    assert len(pages) == len(set(pages))  # no page extracted twice


def test_a_changed_file_is_re_examined(client, req_id, workspace, monkeypatch):
    """Everything remembered about a file is keyed on its (size, mtime) state, so
    replacing bytes re-does the work — an index entry that survived a byte change
    would silently stop a swapped-in bulk file from splitting."""
    from resume_screener import batch_prep

    _upload(client, req_id, [1])
    folder = workspace / "data" / "roles" / req_id / "resumes"
    landed = next(p for p in folder.iterdir() if p.suffix == ".pdf")
    import json
    index = json.loads((folder / batch_prep.INDEX_NAME).read_text(encoding="utf-8"))
    assert batch_prep._entry_valid(folder, landed.name, index["files"][landed.name])

    from tests.test_resume_split import _bulk_pdf

    landed.write_bytes(_bulk_pdf())  # same path, different bytes
    assert not batch_prep._entry_valid(folder, landed.name, index["files"][landed.name])
    assert local_folder.cached_candidate_id(landed) == local_folder.candidate_id_for(
        landed.read_bytes())

    # …and the next worker session re-examines it as an unknown file and splits it,
    # rather than trusting the stale entry.
    assert batch_prep.run_worker(req_id, folder) == 0
    state = batch_prep.read_state(folder)
    assert state["split_files"] == 1 and state["split_resumes"] == 3
