"""P18 per-role resume batches: slugs, content-hash dedupe on import, and the
ATS port reading each requisition's own folder (addendum 05 Part B)."""

from __future__ import annotations

import pytest

from resume_screener.ats.local_folder import (
    BatchError,
    LocalFolderATS,
    add_bytes_to_batch,
    batch_dir,
    batch_files,
    clear_batch,
    import_folder_into_batch,
    role_slug,
)
from resume_screener.models import Requisition


def _req(title: str) -> Requisition:
    # req_id == role_slug(title) — the creation invariant (jds/<slug>.md is the
    # stem). Batches are keyed by this IMMUTABLE handle since B3, so a title
    # edit can never orphan a role's résumés.
    return Requisition(req_id=role_slug(title), role_title=title,
                       jd_ref="jds/x.md", job_location="IN")


# --- slug generation ----------------------------------------------------------------


def test_role_slug_basic():
    assert role_slug("Accountant - Treasury Accounting") == "accountant-treasury-accounting"


def test_role_slug_em_dash_punctuation_and_case():
    assert role_slug("Finance Manager — Mumbai (Sales!)") == "finance-manager-mumbai-sales"


def test_role_slug_never_empty():
    assert role_slug("—…—") == "role"
    assert role_slug("") == "role"


def test_batch_dir_shape(tmp_path):
    assert batch_dir(tmp_path, "Finance Manager") == tmp_path / "finance-manager" / "resumes"


# --- dedupe on import ---------------------------------------------------------------


def _make_source(tmp_path):
    src = tmp_path / "incoming"
    (src / "nested").mkdir(parents=True)
    (src / "alpha.pdf").write_bytes(b"resume-A")
    (src / "beta.pdf").write_bytes(b"resume-A")          # same CONTENT, different name
    (src / "gamma.docx").write_bytes(b"resume-B")
    (src / "notes.txt").write_bytes(b"not a resume")     # unsupported
    (src / "nested" / "delta.pdf").write_bytes(b"resume-C")  # found recursively
    (src / "~$lock.docx").write_bytes(b"word lock file")  # skipped silently
    return src


def test_import_dedupes_by_content_and_reports(tmp_path):
    batch = tmp_path / "batch"
    report = import_folder_into_batch(_make_source(tmp_path), batch)
    assert (report.added, report.duplicates, report.unsupported) == (3, 1, 1)
    assert report.errors == []
    assert report.sentence() == "3 added, 1 duplicate skipped, 1 unsupported skipped"
    assert {p.name for p in batch_files(batch)} == {"alpha.pdf", "gamma.docx", "delta.pdf"}


def test_reimport_is_all_duplicates(tmp_path):
    batch = tmp_path / "batch"
    src = _make_source(tmp_path)
    import_folder_into_batch(src, batch)
    again = import_folder_into_batch(src, batch)
    assert (again.added, again.duplicates, again.unsupported) == (0, 4, 1)


def test_same_name_different_content_gets_a_free_name(tmp_path):
    batch = tmp_path / "batch"
    assert add_bytes_to_batch("cv.pdf", b"one", batch) == "added"
    assert add_bytes_to_batch("cv.pdf", b"two", batch) == "added"
    assert {p.name for p in batch_files(batch)} == {"cv.pdf", "cv-2.pdf"}


def test_unsupported_extension_rejected_before_writing(tmp_path):
    batch = tmp_path / "batch"
    assert add_bytes_to_batch("cv.txt", b"text", batch) == "unsupported"
    assert batch_files(batch) == []


def test_import_bad_paths_raise_friendly_errors(tmp_path):
    with pytest.raises(BatchError, match="doesn't exist"):
        import_folder_into_batch(tmp_path / "nope", tmp_path / "batch")
    lone = tmp_path / "file.pdf"
    lone.write_bytes(b"x")
    with pytest.raises(BatchError, match="not a folder"):
        import_folder_into_batch(lone, tmp_path / "batch")
    target = tmp_path / "roles" / "x" / "resumes"
    target.mkdir(parents=True)
    with pytest.raises(BatchError, match="already this role's batch"):
        import_folder_into_batch(target, target)


def test_clear_batch_counts(tmp_path):
    batch = tmp_path / "batch"
    add_bytes_to_batch("a.pdf", b"a", batch)
    add_bytes_to_batch("b.pdf", b"b", batch)
    assert clear_batch(batch) == 2
    assert batch_files(batch) == []


# --- the ATS port reads per-role folders ----------------------------------------------


def _ats(tmp_path, override=None):
    return LocalFolderATS(
        jd_dir=tmp_path / "jds",
        resumes_dir=tmp_path / "legacy",
        default_job_location="IN",
        roles_dir=tmp_path / "roles",
        resumes_override=override,
    )


def test_fetch_candidates_reads_that_roles_batch_only(tmp_path):
    for title, blob in (("Role A", b"candidate-A"), ("Role B", b"candidate-B")):
        folder = batch_dir(tmp_path / "roles", title)
        folder.mkdir(parents=True)
        (folder / f"{blob.decode()}.pdf").write_bytes(blob)
    ats = _ats(tmp_path)
    a = ats.fetch_candidates(_req("Role A"))
    b = ats.fetch_candidates(_req("Role B"))
    assert [c.label for c in a] == ["candidate-A"]
    assert [c.label for c in b] == ["candidate-B"]
    # fetch_documents resolves each id back to a path inside the ROLE's folder
    doc = ats.fetch_documents(a[0])[0]
    assert "role-a" in doc.uri.replace("\\", "/")


def test_empty_batch_falls_back_to_legacy_folder(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "old.pdf").write_bytes(b"legacy-resume")
    ats = _ats(tmp_path)
    candidates = ats.fetch_candidates(_req("Brand New Role"))
    assert [c.label for c in candidates] == ["old"]


def test_batch_wins_over_legacy_when_it_has_files(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "old.pdf").write_bytes(b"legacy-resume")
    folder = batch_dir(tmp_path / "roles", "Role A")
    folder.mkdir(parents=True)
    (folder / "new.pdf").write_bytes(b"batch-resume")
    ats = _ats(tmp_path)
    assert [c.label for c in ats.fetch_candidates(_req("Role A"))] == ["new"]


def test_explicit_override_beats_everything(tmp_path):
    override = tmp_path / "elsewhere"
    override.mkdir()
    (override / "special.pdf").write_bytes(b"override-resume")
    folder = batch_dir(tmp_path / "roles", "Role A")
    folder.mkdir(parents=True)
    (folder / "new.pdf").write_bytes(b"batch-resume")
    ats = _ats(tmp_path, override=override)
    assert [c.label for c in ats.fetch_candidates(_req("Role A"))] == ["special"]
