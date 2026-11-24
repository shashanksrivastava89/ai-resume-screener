"""Layer-2 enforcement tests (P9): the code-not-prompt-trust guarantees. Every
rule in layer2.enforce_bounds gets a hostile input. Offline (fake client)."""

import json
from types import SimpleNamespace

from resume_screener.cache import JsonCache
from resume_screener.features import FeatureSet, CertificationStats, TenureStats, TrajectoryStats
from resume_screener.findings import FindingsRecord
from resume_screener.layer2 import (
    AdjustmentDraft,
    Layer2Draft,
    ProposedSignalDraft,
    enforce_bounds,
    run_layer2,
)
from resume_screener.models import EnabledSignal, ExcludedSignal, Finding, ResumeProfile

TEXT = "Owns treasury close for EMEA entities. Filed GST monthly. 112% of FY24 target."


def _signal(sid: str, direction="both", cap=4.0) -> EnabledSignal:
    return EnabledSignal(
        id=sid, description="d", kind="judged", direction=direction,
        cap_points=cap, enabled_default=True, job_relatedness="j", params={},
    )


ENABLED = [
    _signal("regulated_environment_exposure", direction="plus", cap=4),
    _signal("tenure_pattern_vs_stability", direction="both", cap=4),
    _signal("quota_attainment_evidence", direction="plus", cap=5),
]


def _draft(sid, points, evidence="Filed GST monthly", rationale="r") -> AdjustmentDraft:
    return AdjustmentDraft(signal_id=sid, points=points, evidence=evidence, rationale=rationale)


# --- the seven enforcement rules, one hostile case each -------------------------------


def test_unknown_or_disabled_signal_dropped():
    kept, notes = enforce_bounds([_draft("culture_fit_vibes", 3)], ENABLED, TEXT, 15)
    assert kept == []
    assert any("not enabled" in n for n in notes)


def test_duplicate_signal_keeps_first_only():
    kept, notes = enforce_bounds(
        [_draft("regulated_environment_exposure", 2), _draft("regulated_environment_exposure", 4)],
        ENABLED, TEXT, 15,
    )
    assert len(kept) == 1 and kept[0].points == 2
    assert any("duplicate" in n for n in notes)


def test_direction_violation_dropped_not_flipped():
    kept, notes = enforce_bounds([_draft("regulated_environment_exposure", -3)], ENABLED, TEXT, 15)
    assert kept == []  # plus-only signal can never subtract — dropped, not clamped to 0
    assert any("plus-only" in n for n in notes)


def test_fabricated_evidence_dropped_whitespace_variant_kept():
    fabricated = _draft("regulated_environment_exposure", 2, evidence="Ran SOX programs globally")
    variant = _draft("quota_attainment_evidence", 3, evidence="112% of\nFY24 target")
    kept, notes = enforce_bounds([fabricated, variant], ENABLED, TEXT, 15)
    assert [a.signal_id for a in kept] == ["quota_attainment_evidence"]
    assert any("not found verbatim" in n for n in notes)


def test_per_signal_clamp_to_effective_cap():
    kept, notes = enforce_bounds([_draft("regulated_environment_exposure", 9)], ENABLED, TEXT, 15)
    assert kept[0].points == 4.0  # clamped to the EFFECTIVE cap, sign preserved
    assert any("clamped" in n for n in notes)


def test_zero_point_adjustment_dropped():
    kept, _ = enforce_bounds([_draft("tenure_pattern_vs_stability", 0)], ENABLED, TEXT, 15)
    assert kept == []


def test_total_clamp_scales_proportionally():
    drafts = [
        _draft("regulated_environment_exposure", 4),
        _draft("quota_attainment_evidence", 5),
        _draft("tenure_pattern_vs_stability", 3),
    ]  # net +12, fine at cap 15 — now tighten the cap to force scaling
    kept, notes = enforce_bounds(drafts, ENABLED, TEXT, total_cap=6)
    net = sum(a.points for a in kept)
    assert abs(net) <= 6.0 + 1e-9
    assert len(kept) == 3  # scaling keeps all evidence; nothing truncated away
    # relative structure preserved: quota (5) still the largest
    assert max(kept, key=lambda a: a.points).signal_id == "quota_attainment_evidence"
    assert any("scaled" in n for n in notes)


def test_mixed_sign_net_within_cap_untouched():
    drafts = [_draft("tenure_pattern_vs_stability", -4),
              _draft("quota_attainment_evidence", 5)]
    kept, notes = enforce_bounds(drafts, ENABLED, TEXT, total_cap=6)
    assert sum(a.points for a in kept) == 1.0  # net +1: no scaling needed
    assert not any("scaled" in n for n in notes)


# --- run_layer2 plumbing: caching + proposals log ----------------------------------------


class FakeClient:
    def __init__(self, parsed):
        self.calls = 0

        def parse(**kwargs):
            self.calls += 1
            message = SimpleNamespace(refusal=None, parsed=parsed)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


def _fixtures():
    profile = ResumeProfile(candidate_id="cid9", source_file="SECRET NAME.pdf", redacted_text=TEXT)
    record = FindingsRecord(
        candidate_id="cid9", role_title="Role", rubric_version=1,
        findings=[Finding(requirement_id="r01", requirement_text="t", match="full", evidence="Filed GST monthly")],
    )
    features = FeatureSet(
        candidate_id="cid9", as_of="2026-07-01", params_used={},
        tenure=TenureStats(permanent_stints=1, excluded_stints=0, undated_stints=0,
                           completed_stints=0, avg_completed_months=None,
                           current_stint_months=24.0, short_stints_in_window=0,
                           short_stint_pattern=False),
        must_have_recency=[], fraction_must_haves_recent=None,
        trajectory=TrajectoryStats(first_level=None, last_level=None, delta=None, dated_roles_considered=0),
        certifications=CertificationStats(total=0, dated_current=0, dated_stale=0, undated=0),
        domain_fit_cosine=0.7,
    )
    return profile, record, features


def test_run_layer2_enforces_caches_and_logs_proposals(tmp_path):
    draft = Layer2Draft(
        adjustments=[
            _draft("quota_attainment_evidence", 9, evidence="112% of FY24 target"),  # will clamp to 5
            _draft("salary_history", 3),  # not enabled -> dropped
        ],
        proposed_signals=[ProposedSignalDraft(name="notice_period_stated", why="useful for planning")],
        narrative="Solid regulated-finance profile.",
    )
    client = FakeClient(draft)
    cache = JsonCache(tmp_path / "cache")
    proposals_file = tmp_path / "proposals.jsonl"
    profile, record, features = _fixtures()

    result, notes, cached = run_layer2(
        profile, record, features, ENABLED, [ExcludedSignal(id="salary_history", reason="x")],
        jd_text="JD", total_cap=15, jurisdiction="IN",
        client=client, model="gpt-test", cache=cache, proposals_path=proposals_file,
    )
    assert not cached and client.calls == 1
    assert [a.signal_id for a in result.adjustments] == ["quota_attainment_evidence"]
    assert result.adjustments[0].points == 5.0
    assert any("clamped" in n for n in notes) and any("not enabled" in n for n in notes)

    # Proposal logged once, with jurisdiction, never applied to adjustments.
    lines = proposals_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["name"] == "notice_period_stated" and logged["jurisdiction"] == "IN"

    # Second run: cache hit — same result + notes, no new model call, NO re-logged proposal.
    result2, notes2, cached2 = run_layer2(
        profile, record, features, ENABLED, [ExcludedSignal(id="salary_history", reason="x")],
        jd_text="JD", total_cap=15, jurisdiction="IN",
        client=client, model="gpt-test", cache=cache, proposals_path=proposals_file,
    )
    assert cached2 and client.calls == 1 and result2 == result and notes2 == notes
    assert len(proposals_file.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_prompt_never_contains_source_file(tmp_path):
    """The candidate's real file name (their NAME) must never reach the model."""
    captured = {}

    class SpyClient:
        def __init__(self):
            def parse(**kwargs):
                captured["messages"] = kwargs["messages"]
                message = SimpleNamespace(
                    refusal=None,
                    parsed=Layer2Draft(adjustments=[], proposed_signals=[], narrative="n"),
                )
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

            self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))

    profile, record, features = _fixtures()
    run_layer2(
        profile, record, features, ENABLED, [], jd_text="JD", total_cap=15,
        jurisdiction="IN", client=SpyClient(), model="gpt-test",
        cache=JsonCache(tmp_path), proposals_path=tmp_path / "p.jsonl",
    )
    full_prompt = json.dumps(captured["messages"])
    assert "SECRET NAME" not in full_prompt
    assert "source_file" not in full_prompt
