"""Batch quality (2026-07-19) + the stage fix that came with it.

1. `service.batch_quality` — the mean final score of the top N gate-passing
   candidates vs the governed `batch_quality` thresholds (pure math over
   synthetic records; no filesystem).
2. The RA-Spec-II regression: "Complete" now means every GATE-PASSING candidate
   is decided — not just the Strong/Possible ones, which flipped a 56-résumé
   req to Complete after a single decision.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _rec(cid: str, score: float, *, eligible: bool = True, band: str = "Possible") -> dict:
    return {"score": {"candidate_id": cid, "final_score": score, "fit_band": band,
                      "layer1": {"eligible": eligible}}}


@pytest.fixture(autouse=True)
def fixed_thresholds(monkeypatch):
    """Pin the batch-quality knobs the arithmetic below assumes (top 5, Strong ≥ 75,
    Okay ≥ 55). They are governed values on the scoring model — the published model
    has since widened the window to the top 7 at Strong ≥ 85 — and this test is
    about the MEAN, not about what a policy maker chose (see tests/packdata.py)."""
    from resume_screener import scoring_model

    real = scoring_model.load_scoring_model()
    pinned = scoring_model.BatchQualityRules(top_n=5, strong_at=75.0, okay_at=55.0)
    monkeypatch.setattr(scoring_model, "load_scoring_model",
                        lambda *a, **k: real.model_copy(update={"batch_quality": pinned}))


# --- the metric itself (pure) ---------------------------------------------------------


def test_batch_quality_is_the_top_n_mean_of_gate_passers():
    from resume_screener.api import service

    records = [_rec("a", 90), _rec("b", 80), _rec("c", 70), _rec("d", 60),
               _rec("e", 50), _rec("f", 40),
               _rec("z", 99, eligible=False)]  # gate-failers never count
    bq = service.batch_quality(records)
    # pinned by the fixture: top_n=5, strong ≥75, okay ≥55
    assert bq["score"] == pytest.approx(70.0)  # (90+80+70+60+50)/5
    assert bq["band"] == "okay" and bq["label"] == "Okay batch"
    assert bq["top_n"] == 5
    assert bq["thresholds"] == {"strong_at": 75.0, "okay_at": 55.0}


def test_batch_quality_bands_and_small_batches():
    from resume_screener.api import service

    strong = service.batch_quality([_rec("a", 90), _rec("b", 80)])
    assert strong["band"] == "strong" and strong["score"] == pytest.approx(85.0)
    assert strong["top_n"] == 2  # fewer than N pass → it averages those

    weak = service.batch_quality([_rec("a", 30)])
    assert weak["band"] == "weak"


def test_batch_quality_with_no_gate_passers_and_no_records():
    from resume_screener.api import service

    assert service.batch_quality([]) is None
    bq = service.batch_quality([_rec("a", 88, eligible=False)])
    assert bq["score"] is None and bq["band"] == "weak"
    assert "hard gates" in bq["basis"]


# --- the stage fix --------------------------------------------------------------------


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
    shutil.copyfile(REPO_ROOT / "signals" / "scoring_model.v1.yaml",
                    tmp_path / "signals" / "scoring_model.v1.yaml")
    monkeypatch.chdir(tmp_path)
    yield tmp_path


def _mk_role_with_run(bands_eligible: list[tuple[str, bool]], run_id: str = "run-1") -> str:
    """One req + one finished run; each entry = (fit_band, gate_passed)."""
    from resume_screener.api import service
    from resume_screener.ats.local_folder import save_req_meta

    stem = "ra-spec"
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# RA Spec\nSpecs.", encoding="utf-8")
    save_req_meta(jd, job_location="IN", created_by="bhavika.test@example.com")
    Path("jds", f"{stem}.rubric.v1.yaml").write_text(
        "role_title: RA Spec\njd_ref: jds/" + stem + ".md\nversion: 1\n"
        "requirements:\n"
        "  - {id: r01, text: GL accounting, type: skill, kind: must_have, weight: 1}\n",
        encoding="utf-8")

    resumes = Path("data/roles") / stem / "resumes"
    resumes.mkdir(parents=True)
    out = Path("output") / stem
    (out / "candidates").mkdir(parents=True)
    real_ids = []
    for i, (band, eligible) in enumerate(bands_eligible):
        f = resumes / f"resume-{i}.pdf"
        f.write_bytes(f"pdf-{i}".encode())
        real = service.cached_candidate_id(f)
        real_ids.append(real)
        (out / "candidates" / f"{real}.json").write_text(json.dumps(
            _rec(real, 70.0, eligible=eligible, band=band)
            | {"findings": None, "features": None}), encoding="utf-8")
    from resume_screener.cache import content_hash

    stamps = service.current_config_stamps(service.config())
    meta = {"run_id": run_id, "role_title": "RA Spec", "jurisdiction": "IN",
            "counts": {"scored": len(real_ids)},
            "versions": {"rubric": f"{stem}.rubric.v1.yaml (v1)",
                         "policy_files": {"base": stamps["policy_base"],
                                          "IN": content_hash(
                                              Path("policies/IN.yaml").read_bytes())[:12]},
                         "scoring_model": stamps["scoring_model"],
                         "preferences": {"applied": []}}}
    (out / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return stem, real_ids


def _decide(req_id: str, run_id: str, cid: str) -> None:
    from resume_screener.feedback import record_decision
    from resume_screener.models import Decision

    record_decision(Decision(candidate_id=cid, run_id=run_id, suggestion="Possible",
                             human_action="agree", final_band="Strong",
                             reason="Move forward", reviewer="bhavika.test@example.com",
                             versions={}, ts="2026-07-19T10:00:00+00:00"))


def test_complete_needs_every_gate_passer_decided(workspace):
    """One Possible + two gate-passing Not-a-fits + one gate-failer: deciding
    only the Possible candidate must read In review (the old rule said
    Complete), and deciding all three gate-passers reads Complete."""
    from resume_screener.api import service

    stem, ids = _mk_role_with_run([("Possible", True), ("Not a fit", True),
                                   ("Not a fit", True), ("Not a fit", False)])
    cfg = service.config()
    req = service.find_requisition(stem, cfg)

    assert service.role_stage(cfg, req)["stage"] == "screened"

    _decide(stem, "run-1", ids[0])  # the single Possible candidate
    assert service.role_stage(cfg, req)["stage"] == "in_review"

    _decide(stem, "run-1", ids[1])
    _decide(stem, "run-1", ids[2])  # every gate-passer now decided
    st = service.role_stage(cfg, req)
    assert st["stage"] == "complete"
    assert st["cta"]["label"] == "View decisions"
    # the batch read rides on the same payload
    assert st["batch_quality"]["top_n"] == 3
