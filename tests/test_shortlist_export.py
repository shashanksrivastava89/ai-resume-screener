"""Shortlist export (2026-07-23): the standalone CSV and the résumé-by-decision
ZIP. One decision-aware plan feeds both, so the CSV's "Resume file" column is
exactly the path each résumé takes inside the ZIP; the ZIP folders candidates by
decision (Move forward / Discuss / Not a fit / Undecided) and carries the CSV at
its top level. Offline: tmp_path workspace + synthetic run output + private map."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from resume_screener.models import candidate_id_for
from tests.test_sso_identity import _as

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
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _record(cid: str) -> dict:
    return {
        "score": {
            "candidate_id": cid, "role_title": "Treasury Analyst", "jurisdiction": "IN",
            "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                       "must_haves_missing": [], "coverage_score": 0.8,
                       "base_score": 80.0, "eligible": True, "reasons": []},
            "layer2": None, "critic": None, "final_score": 80.0,
            "fit_band": "Strong", "confidence": "high", "summary_rationale": "ok",
        },
        "findings": {"candidate_id": cid, "role_title": "Treasury Analyst",
                     "rubric_version": 1, "findings": [
                         {"requirement_id": "r01", "requirement_text": "US GAAP",
                          "match": "full", "evidence": "US GAAP", "note": None}]},
        "suspended_adjustments": [],
    }


@pytest.fixture()
def screened_role(client) -> dict:
    """One screened role with three attached candidates + a private map (the
    id->résumé-file bridge the export resolves filenames through)."""
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9010",
    })
    req_id = created.json()["req_id"]
    client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"), json={
        "requirements": [{"text": "US GAAP", "kind": "must_have", "importance": 4}]})

    folder = Path(client.get(f"/api/roles/{req_id}/batch",
                             headers=_as("TASpecialist")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    names = ("Anita Rao.pdf", "Vikram Shah.pdf", "Meera Roach.pdf")
    cids, private = [], {}
    for name in names:
        data = name.encode("utf-8")
        (folder / name).write_bytes(data)
        cid = candidate_id_for(data)
        cids.append(cid)
        private[cid] = str(folder / name)

    out = Path("output") / req_id
    (out / "candidates").mkdir(parents=True)
    for cid in cids:
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps(_record(cid)), encoding="utf-8")
    (out / "private").mkdir(parents=True)
    (out / "private" / "candidate_map.json").write_text(
        json.dumps(private), encoding="utf-8")

    from resume_screener.cache import content_hash

    (out / "run_meta.json").write_text(json.dumps({
        "run_id": "run-1", "started_utc": "2026-07-12T08:00:00+00:00",
        "jurisdiction": "IN", "policy_pack": "IN", "counts": {"scored": len(cids)},
        "versions": {
            "rubric": f"{req_id}.rubric.v1.yaml (v1)",
            "policy_files": {
                "base": content_hash(Path("policies/base.yaml").read_bytes())[:12],
                "IN": content_hash(Path("policies/IN.yaml").read_bytes())[:12],
            },
        },
    }), encoding="utf-8")
    return {"req_id": req_id, "cids": cids, "names": names}


def _decide(client, req_id, cid, action, reason="ok"):
    r = client.post(f"/api/roles/{req_id}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": cid, "action": action, "reason": reason})
    assert r.status_code == 200, r.text


def test_csv_has_resume_path_column_matching_the_zip_layout(client, screened_role):
    req_id, cids, names = screened_role["req_id"], screened_role["cids"], screened_role["names"]
    _decide(client, req_id, cids[0], "forward")   # Anita -> Move forward
    _decide(client, req_id, cids[1], "not")        # Vikram -> Not a fit
    # cids[2] left undecided

    r = client.get(f"/api/roles/{req_id}/shortlist/export.csv", headers=_as("TASpecialist"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "shortlist-2026-9010.csv" in r.headers.get("content-disposition", "")

    body = r.content.decode("utf-8")
    assert "Resume file" in body.splitlines()[0]
    # the path column is <bucket>/<filename>, incl. the undecided folder
    # 2026-09-07: the filename carries the decision record, because a résumé that
    # leaves this app has no database behind it. Bucket folder + name + req + call.
    assert "Move forward/Anita Rao — 2026-9010 — Move forward — " in body
    assert "Not a fit/Vikram Shah — 2026-9010 — Not a fit — " in body
    assert "Undecided/Meera Roach — 2026-9010.pdf" in body  # no decision, no call/date


def test_download_zip_folders_by_decision_and_includes_the_csv(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    _decide(client, req_id, cids[0], "forward")
    _decide(client, req_id, cids[1], "not")

    r = client.post(f"/api/roles/{req_id}/shortlist/download", headers=_as("TASpecialist"),
                    json={"candidate_ids": cids})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "shortlist-2026-9010.zip" in r.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = sorted(zf.namelist())
        assert "shortlist-2026-9010.csv" in names          # the export file, top level
        forward = next(n for n in names if n.startswith("Move forward/Anita Rao"))
        assert "2026-9010" in forward and "Move forward" in forward
        assert any(n.startswith("Not a fit/Vikram Shah") for n in names)
        assert any(n.startswith("Undecided/Meera Roach") for n in names)
        # the bytes are the ORIGINAL file: these fixtures aren't real PDFs, so the
        # metadata stamp declines and ships them untouched rather than mangling them
        assert zf.read(forward) == b"Anita Rao.pdf"


def test_download_only_selected_candidates(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    _decide(client, req_id, cids[0], "forward")

    r = client.post(f"/api/roles/{req_id}/shortlist/download", headers=_as("TASpecialist"),
                    json={"candidate_ids": [cids[0]]})
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        resumes = [n for n in zf.namelist() if not n.endswith(".csv")]
        assert len(resumes) == 1 and resumes[0].startswith("Move forward/Anita Rao")


def test_download_empty_selection_is_400(client, screened_role):
    req_id = screened_role["req_id"]
    r = client.post(f"/api/roles/{req_id}/shortlist/download", headers=_as("TASpecialist"),
                    json={"candidate_ids": []})
    assert r.status_code == 400
