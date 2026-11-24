"""P35: service.screen_preflight — pure change detection over synthetic
output/<role>/ fixtures in tmp_path. No Azure, no engine calls: the detector
only compares stamps and content hashes, mirroring the engine's cache."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener.cache import content_hash
from resume_screener.models import Requisition, candidate_id_for

REPO_ROOT = Path(__file__).resolve().parents[1]
STEM = "finance-analyst"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """config.yaml + policies/ + signals/ + jds/ under tmp_path — everything the
    detector hashes must exist locally so edits here can't touch the repo."""
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    for folder in ("policies",):
        (tmp_path / folder).mkdir()
        for f in sorted((REPO_ROOT / folder).glob("*.yaml")):
            shutil.copyfile(f, tmp_path / folder / f.name)
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "scoring_model.v1.yaml",
                    tmp_path / "signals" / "scoring_model.v1.yaml")
    (tmp_path / "jds").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _req(location="IN") -> Requisition:
    return Requisition(req_id=STEM, role_title="Finance Analyst",
                       jd_ref=f"jds/{STEM}.md", job_location=location)


def _write_rubric(version=1):
    (Path("jds") / f"{STEM}.rubric.v{version}.yaml").write_text(
        "role_title: Finance Analyst\n"
        f"jd_ref: jds/{STEM}.md\n"
        f"version: {version}\n"
        "requirements:\n"
        "- {id: r01, text: US GAAP, type: skill, kind: must_have, weight: 1.0}\n",
        encoding="utf-8")


def _batch_file(cfg, req, name: str, data: bytes) -> str:
    from resume_screener.api import service

    folder = service.batch_folder(cfg, req)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return candidate_id_for(data)


def _stamps(cfg, rubric_version=1) -> dict:
    """Compose the versions block EXACTLY like cli.run stamps it."""
    from resume_screener.scoring_model import scoring_model_stamp

    return {
        "rubric": f"{STEM}.rubric.v{rubric_version}.yaml (v{rubric_version})",
        "scoring_model": scoring_model_stamp(),
        "policy_files": {
            "base": content_hash(Path("policies/base.yaml").read_bytes())[:12],
            "IN": content_hash(Path("policies/IN.yaml").read_bytes())[:12],
        },
    }


def _write_run(cfg, scored: dict[str, str], rubric_version=1, jurisdiction="IN"):
    """run_meta + per-candidate records + the private map {cid: source path}."""
    out = Path("output") / STEM
    (out / "candidates").mkdir(parents=True, exist_ok=True)
    for cid in scored:
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps({"score": {"candidate_id": cid}}), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": "run-1", "started_utc": "2026-07-12T08:00:00+00:00",
        "jurisdiction": jurisdiction, "policy_pack": jurisdiction,
        "counts": {"scored": len(scored)},
        "versions": _stamps(cfg, rubric_version),
    }), encoding="utf-8")
    (out / "private").mkdir(exist_ok=True)
    (out / "private" / "candidate_map.json").write_text(
        json.dumps(scored), encoding="utf-8")


def _preflight(req=None):
    from resume_screener.api import service

    cfg = service.config()
    return cfg, service.screen_preflight(cfg, req or _req())


def test_empty_batch(workspace):
    _write_rubric()
    _cfg, pre = _preflight()
    assert pre["will_run"] is False and pre["reason"] == "empty_batch"
    assert pre["files"] == [] and pre["counts"]["total"] == 0


def test_never_run(workspace):
    from resume_screener.api import service

    _write_rubric()
    cfg = service.config()
    _batch_file(cfg, _req(), "Anita Rao.pdf", b"anita-v1")
    _cfg, pre = _preflight()
    assert pre["will_run"] is True and pre["reason"] == "never_run"
    assert pre["files"] == [{"name": "Anita Rao.pdf", "status": "new"}]
    assert pre["changed"] == {"checklist": False, "rules": False, "location": False,
                              "scoring_model": False, "preferences": False}


def test_nothing_changed(workspace):
    from resume_screener.api import service

    _write_rubric()
    cfg = service.config()
    req = _req()
    cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")})
    _cfg, pre = _preflight()
    assert pre["will_run"] is False and pre["reason"] == "nothing_changed"
    assert pre["files"] == [{"name": "Anita Rao.pdf", "status": "cached"}]
    assert pre["counts"] == {"total": 1, "new": 0, "updated": 0, "cached": 1, "removed": 0}


def test_one_new_file(workspace):
    from resume_screener.api import service

    _write_rubric()
    cfg = service.config()
    req = _req()
    cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")})
    _batch_file(cfg, req, "Vikram Shah.pdf", b"vikram-v1")
    _cfg, pre = _preflight()
    assert pre["will_run"] is True and pre["reason"] == "files_changed"
    assert pre["counts"] == {"total": 2, "new": 1, "updated": 0, "cached": 1, "removed": 0}


def test_same_basename_different_bytes_is_updated(workspace):
    from resume_screener.api import service

    _write_rubric()
    cfg = service.config()
    req = _req()
    old_cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {old_cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")})
    # the recruiter re-exports the résumé: same name, different bytes = new id
    _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v2-re-exported")
    _cfg, pre = _preflight()
    assert pre["reason"] == "files_changed"
    assert pre["files"] == [{"name": "Anita Rao.pdf", "status": "updated"}]
    # the old id is informationally "removed" (it left the batch with the re-export)
    assert pre["counts"]["updated"] == 1 and pre["counts"]["removed"] == 1


def test_rubric_bump_is_config_changed_checklist(workspace):
    from resume_screener.api import service

    _write_rubric(1)
    cfg = service.config()
    req = _req()
    cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")},
               rubric_version=1)
    _write_rubric(2)  # the recruiter edited + republished the checklist
    _cfg, pre = _preflight()
    assert pre["will_run"] is True and pre["reason"] == "config_changed"
    assert pre["changed"]["checklist"] is True
    assert pre["changed"]["rules"] is False and pre["changed"]["location"] is False


def test_signal_edit_is_a_scoring_model_change(workspace):
    """A signal-cap edit publishes the next scoring-model version (the merge) —
    preflight reports it on the scoring_model axis, not "rules"."""
    from resume_screener.api import service
    from resume_screener import governance
    from resume_screener.scoring_model import canonical_dump, load_scoring_model

    _write_rubric()
    cfg = service.config()
    req = _req()
    cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")})
    data = canonical_dump(load_scoring_model())
    next(s for s in data["signals"] if s["id"] == "skill_recency")["cap_points"] = 4.0
    governance.publish_scoring_model(data, author="admin@x", rationale="calibration")
    _cfg, pre = _preflight()
    assert pre["reason"] == "config_changed"
    assert pre["changed"]["scoring_model"] is True
    assert pre["changed"]["rules"] is False


def test_pack_edit_is_rules_and_location_change_is_location(workspace):
    from resume_screener.api import service

    _write_rubric()
    cfg = service.config()
    req = _req()
    cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")})

    pack = Path("policies/IN.yaml")
    pack.write_text(pack.read_text(encoding="utf-8") + "\n# tightened after the run\n",
                    encoding="utf-8")
    _cfg, pre = _preflight()
    assert pre["reason"] == "config_changed" and pre["changed"]["rules"] is True

    # a moved role re-screens everything under the new location's pack
    _cfg, moved = _preflight(_req(location="EU"))
    assert moved["reason"] == "config_changed" and moved["changed"]["location"] is True


def test_preference_change_is_config_changed(workspace):
    """P40: an HR preference that becomes applicable AFTER the run (the owner
    adds/gets one approved) is a config change — the ±2 nudge would now land, so
    a fresh screen must be offered instead of 'nothing changed'."""
    from resume_screener.api import service
    from resume_screener import preferences
    from resume_screener.ats.local_folder import save_req_meta

    _write_rubric()
    cfg = service.config()
    req = _req()
    # the sidecar names the owner whose active preferences apply to this req
    save_req_meta(req.jd_ref, created_by="owner@example.com")
    cid = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    _write_run(cfg, {cid: str(service.batch_folder(cfg, req) / "Anita Rao.pdf")})

    # baseline: the run had no preferences and none exist now → nothing changed
    _cfg, pre0 = _preflight()
    assert pre0["reason"] == "nothing_changed"
    assert pre0["changed"]["preferences"] is False

    # the owner adds a benign preference after the run: it waits for sign-off, so
    # nothing a screen would use has changed yet (2026-10-16, pen-test RS-01)
    added = preferences.add_preference("owner@example.com", "Prefers strong Excel modelling")
    assert added["display_status"] == "pending"
    _cfg, pre_pending = _preflight()
    assert pre_pending["changed"]["preferences"] is False

    preferences.approve([added["pref_id"]], approver="head@example.com")
    _cfg, pre1 = _preflight()
    assert pre1["will_run"] is True and pre1["reason"] == "config_changed"
    assert pre1["changed"]["preferences"] is True
    # the other config axes are untouched
    assert pre1["changed"]["checklist"] is False and pre1["changed"]["rules"] is False


def test_removed_is_informational_only(workspace):
    from resume_screener.api import service

    _write_rubric()
    cfg = service.config()
    req = _req()
    kept = _batch_file(cfg, req, "Anita Rao.pdf", b"anita-v1")
    folder = service.batch_folder(cfg, req)
    gone = _batch_file(cfg, req, "Vikram Shah.pdf", b"vikram-v1")
    _write_run(cfg, {kept: str(folder / "Anita Rao.pdf"),
                     gone: str(folder / "Vikram Shah.pdf")})
    (folder / "Vikram Shah.pdf").unlink()  # removed from the batch after the run

    _cfg, pre = _preflight()
    assert pre["reason"] == "nothing_changed" and pre["will_run"] is False
    assert pre["counts"]["removed"] == 1
    assert pre["counts"]["cached"] == 1
