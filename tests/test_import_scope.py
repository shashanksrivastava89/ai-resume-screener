"""Folder import reads only the shared résumés folder (2026-10-16).

POST /api/roles/{id}/batch/import exists to copy résumés out of the legacy shared
folder (cfg.ats.resumes_dir, data/resumes). It used to copy from any folder the
server could read — another requisition's batch included — so a recruiter could
pull candidates they were never given. Offline: tmp_path workspace, inline worker.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.test_batch_download import _mk_role, _upload, client, workspace  # noqa: F401
from tests.test_sso_identity import _as

OUTSIDE = "Import only from the shared résumés folder (data/resumes)."


def _import(client, req_id, folder):
    return client.post(f"/api/roles/{req_id}/batch/import", headers=_as("TASpecialist"),
                       json={"folder": folder})


def _names(client, req_id) -> set[str]:
    files = client.get(f"/api/roles/{req_id}/batch", headers=_as("TASpecialist")).json()["files"]
    return {f["name"] if isinstance(f, dict) else str(f) for f in files}


@pytest.fixture()
def shared(workspace):
    root = workspace / "data" / "resumes"
    (root / "sub").mkdir(parents=True)
    (root / "a.pdf").write_bytes(b"%PDF-1.4 shared A")
    (root / "sub" / "b.pdf").write_bytes(b"%PDF-1.4 shared B")
    return root


@pytest.mark.parametrize("folder", ["data/resumes", "ABSOLUTE", "data/resumes/sub"])
def test_the_shared_folder_imports(client, shared, folder):
    req_id = _mk_role(client)
    target = str(shared) if folder == "ABSOLUTE" else folder
    r = _import(client, req_id, target)
    assert r.status_code == 200, r.text
    expected = {"b.pdf"} if folder.endswith("sub") else {"a.pdf", "b.pdf"}
    assert _names(client, req_id) == expected


def test_another_requisitions_batch_is_refused(client, shared, workspace):
    other = _mk_role(client, title="Treasury Analyst", icims="IN-901")
    _upload(client, other, "theirs.pdf", b"%PDF-1.4 someone else's candidate")
    mine = _mk_role(client)
    for folder in (f"data/roles/{other}/resumes", str(workspace / "data" / "roles"),
                   "data/resumes/../roles", str(workspace), "C:/" if os.name == "nt" else "/"):
        r = _import(client, mine, folder)
        assert r.status_code == 400 and r.json()["detail"] == OUTSIDE, folder
    assert _names(client, mine) == set()


def test_errors_never_echo_a_server_path(client, shared, workspace):
    req_id = _mk_role(client)
    missing = _import(client, req_id, "data/resumes/nope")
    assert missing.status_code == 400 and missing.json()["detail"] == "That folder doesn't exist."
    a_file = _import(client, req_id, "data/resumes/a.pdf")
    assert a_file.status_code == 400 and a_file.json()["detail"] == "That's a file, not a folder."
    # outside the folder, even a path that doesn't exist gets the same answer
    probe = _import(client, req_id, str(workspace / "no-such-dir"))
    assert probe.json()["detail"] == OUTSIDE
    for r in (missing, a_file, probe):
        assert str(workspace) not in r.text and "nope" not in r.json()["detail"]


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_out_of_the_shared_folder_is_skipped(client, shared, workspace):
    secret = workspace / "elsewhere"
    secret.mkdir()
    (secret / "c.pdf").write_bytes(b"%PDF-1.4 not shared")
    (shared / "link").symlink_to(secret, target_is_directory=True)
    (shared / "c-link.pdf").symlink_to(secret / "c.pdf")
    req_id = _mk_role(client)
    assert _import(client, req_id, "data/resumes").status_code == 200
    assert _names(client, req_id) == {"a.pdf", "b.pdf"}
