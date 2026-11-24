"""Report tests (P11): files written, exact CSV columns, ranking rule, HTML
escaping of untrusted resume text, run_meta round-trip."""

import json

from resume_screener.findings import FindingsRecord
from resume_screener.models import (
    Adjustment,
    CriticReview,
    Finding,
    Layer1Result,
    Layer2Result,
    PreferenceAdjustment,
    Requirement,
    Rubric,
    ScoreResult,
)
from resume_screener.report import CandidateBundle, rank_bundles, top_gaps, write_reports

RUBRIC = Rubric(
    role_title="Role", jd_ref="jd.md", version=1,
    requirements=[
        Requirement(id="r01", text="US GAAP", kind="must_have", weight=3),
        Requirement(id="r02", text="Oracle EBS", kind="nice_to_have", weight=1),
    ],
)


def _bundle(cid: str, band: str, final: float, base: float, narrative: str = "n") -> CandidateBundle:
    layer1 = Layer1Result(candidate_id=cid, rubric_version=1, coverage_score=base / 100,
                          base_score=base, eligible=band != "gate", reasons=[])
    layer2 = Layer2Result(
        candidate_id=cid,
        adjustments=[Adjustment(signal_id="s1", points=final - base, evidence="<b>quote</b>",
                                rationale="r")] if final != base else [],
        narrative=narrative,
    )
    critic = CriticReview(verdict_per_adjustment={"s1": "pass"} if final != base else {})
    score = ScoreResult(
        candidate_id=cid, role_title="Role", jurisdiction="IN",
        layer1=layer1, layer2=layer2, critic=critic,
        final_score=final, fit_band="Strong" if band == "Strong" else ("Possible" if band == "Possible" else "Not a fit"),
        confidence="high", summary_rationale="ok",
    )
    findings = FindingsRecord(
        candidate_id=cid, role_title="Role", rubric_version=1,
        findings=[
            Finding(requirement_id="r01", requirement_text="US GAAP", match="partial", evidence="e"),
            Finding(requirement_id="r02", requirement_text="Oracle EBS", match="none"),
        ],
    )
    return CandidateBundle(score=score, findings=findings, extractor="document-intelligence",
                           extraction_confidence=0.96, pages=1)


def test_top_gaps_orders_by_weight():
    gaps = top_gaps(_bundle("c1", "Strong", 80, 75), RUBRIC)
    assert gaps == ["US GAAP (partial)", "Oracle EBS (none)"]  # weight 3 gap first


def test_ranking_band_first_then_score():
    bundles = [
        _bundle("low-strong", "Strong", 76, 76),
        _bundle("high-possible", "Possible", 74, 74),
        _bundle("high-strong", "Strong", 90, 85),
    ]
    ranked = [b.score.candidate_id for b in rank_bundles(bundles)]
    # A Possible with 74 must NOT outrank a Strong with 76.
    assert ranked == ["high-strong", "low-strong", "high-possible"]


def test_write_reports_files_columns_escaping(tmp_path):
    bundles = [
        _bundle("cid-a", "Strong", 90, 85),
        # hostile narrative: must be ESCAPED in the html, never executed
        _bundle("cid-b", "Possible", 60, 60, narrative="<script>alert('xss')</script>"),
    ]
    run_meta = {"run_id": "test-1", "jurisdiction": "IN", "policy_pack": "IN",
                "obligations": ["A human decides."]}
    paths = write_reports(tmp_path, bundles, RUBRIC, run_meta)

    for key in ("xlsx", "csv", "html", "run_meta"):
        assert paths[key].exists(), key
    assert (tmp_path / "candidates" / "cid-a.json").exists()

    header = paths["csv"].read_text(encoding="utf-8").splitlines()[0]
    assert header == ("rank,candidate_id,band,final,base,adj_total,pref_total,confidence,"
                      "must_haves_missing,top_gaps,critic_flags")

    html_text = paths["html"].read_text(encoding="utf-8")
    assert "<script>alert" not in html_text  # escaped...
    assert "&lt;script&gt;alert" in html_text  # ...but present as text
    assert "&lt;b&gt;quote&lt;/b&gt;" in html_text  # evidence quotes escaped too
    assert "Jurisdiction: IN" in html_text  # the badge

    meta = json.loads(paths["run_meta"].read_text(encoding="utf-8"))
    assert meta["jurisdiction"] == "IN" and meta["obligations"]

    candidate = json.loads((tmp_path / "candidates" / "cid-a.json").read_text(encoding="utf-8"))
    assert candidate["score"]["final_score"] == 90
    assert candidate["extraction"]["extractor"] == "document-intelligence"


def test_preference_adjustments_round_trip_and_split_totals(tmp_path):
    """P40: preference_adjustments survive to the per-candidate JSON, and the CSV
    splits the delta into adj_total (Layer-2 only) + pref_total."""
    bundle = _bundle("cid-p", "Strong", 88, 80)  # +8 L2 delta before prefs
    bundle.score.preference_adjustments = [
        PreferenceAdjustment(pref_id="p1", owner="ta@x", text="prefers BI depth",
                             points=2.0, evidence="q", rationale="r"),
    ]
    # final now reflects base + L2 + pref; keep the arithmetic simple for the row math
    bundle.score.final_score = 90  # 80 base + 8 L2 + 2 pref (illustrative)
    paths = write_reports(tmp_path, [bundle], RUBRIC, {"run_id": "r", "obligations": []})

    row = paths["csv"].read_text(encoding="utf-8").splitlines()[1].split(",")
    header = paths["csv"].read_text(encoding="utf-8").splitlines()[0].split(",")
    cell = dict(zip(header, row))
    assert cell["pref_total"] == "2.0"
    assert cell["adj_total"] == "8.0"  # final(90) - base(80) - pref(2) = L2-only 8

    candidate = json.loads((tmp_path / "candidates" / "cid-p.json").read_text(encoding="utf-8"))
    prefs = candidate["score"]["preference_adjustments"]
    assert len(prefs) == 1 and prefs[0]["pref_id"] == "p1" and prefs[0]["points"] == 2.0
