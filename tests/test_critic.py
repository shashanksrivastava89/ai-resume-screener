"""Critic tests (P10 + the 2026-07-18 merge): the governed code screen's
precision AND recall, verdict merging, suspension consequences, narrative-stage
screening, and the monitors stub. Offline (fake client)."""

from types import SimpleNamespace

from resume_screener.cache import JsonCache
from resume_screener.critic import (
    AdjustmentVerdictDraft,
    CriticDraft,
    apply_critic,
    code_screen,
    four_fifths_monitor,
    narrative_flags,
    run_critic,
)
from resume_screener.models import Adjustment, EnabledSignal, Layer2Result
from resume_screener.scoring_model import default_scoring_model

TOPICS = default_scoring_model().topics


def _adjustment(sid="tenure_pattern_vs_stability", points=2.0, rationale="solid tenure", evidence="e") -> Adjustment:
    return Adjustment(signal_id=sid, points=points, evidence=evidence, rationale=rationale)


def _signal(sid) -> EnabledSignal:
    return EnabledSignal(id=sid, description="d", kind="judged", direction="both",
                         cap_points=4, enabled_default=True, job_relatedness="j", params={})


def _topics(*ids):
    return [t for t in TOPICS if t.id in ids]


class FakeClient:
    def __init__(self, parsed):
        self.calls = 0

        def parse(**kwargs):
            self.calls += 1
            message = SimpleNamespace(refusal=None, parsed=parsed)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


# --- code screen: recall on the governed never-scored topics ---------------------------


def test_code_screen_catches_never_scored_topics():
    assert "employment_gaps" in code_screen(_adjustment(rationale="stable career without employment gaps"), TOPICS)
    assert "unemployment_status" in code_screen(_adjustment(rationale="currently unemployed, so risky"), TOPICS)
    assert "overqualification_or_age_proxies" in code_screen(_adjustment(rationale="graduation year suggests age fit"), TOPICS)
    assert "salary_history" in code_screen(_adjustment(rationale="low current CTC means motivated"), TOPICS)
    assert "school_prestige_as_such" in code_screen(_adjustment(rationale="IIM pedigree is impressive"), TOPICS)
    assert "demographic_language" in code_screen(_adjustment(rationale="as a female candidate in sales..."), TOPICS)
    assert "culture_fit_vibes" in code_screen(_adjustment(rationale="good culture fit for the team"), TOPICS)
    # default topics = the current scoring model's (same list here)
    assert "employment_gaps" in code_screen(_adjustment(rationale="an unexplained gap in 2021"))


# --- code screen: precision on legitimate finance vocabulary ---------------------------


def test_code_screen_spares_finance_vocabulary():
    assert code_screen(_adjustment(rationale="performed gap analysis of SOX controls"), TOPICS) == []
    assert code_screen(_adjustment(rationale="processed monthly salary JV postings for payroll"), TOPICS) == []
    assert code_screen(_adjustment(rationale="addresses reconciliation issues quickly"), TOPICS) == []
    assert code_screen(_adjustment(rationale="coverage gaps in the control matrix were remediated"), TOPICS) == []
    assert code_screen(_adjustment(evidence="average of 15 entities managed", rationale="broad scope"), TOPICS) == []


# --- the required unit test: never-scored-topic rationale gets suspended ----------------


def test_excluded_basis_rationale_suspended_even_if_model_passes(tmp_path):
    """THE P10 acceptance test: the model says 'pass', but the rationale cites an
    employment gap — the code screen must force suspend."""
    poisoned = Layer2Result(
        candidate_id="cid1",
        adjustments=[_adjustment(rationale="no employment gaps in the last 5 years", points=3)],
        narrative="n",
    )
    lenient_model = FakeClient(CriticDraft(
        verdicts=[AdjustmentVerdictDraft(signal_id="tenure_pattern_vs_stability", verdict="pass", note=None)],
        overall_flags=[],
    ))
    review, _ = run_critic(poisoned, [_signal("tenure_pattern_vs_stability")],
                           _topics("employment_gaps"),
                           lenient_model, "gpt-test", JsonCache(tmp_path))
    assert review.verdict_per_adjustment["tenure_pattern_vs_stability"] == "suspend"
    assert any("code screen" in n for n in review.notes)

    surviving, suspended = apply_critic(poisoned, review)
    assert surviving == [] and len(suspended) == 1  # removed from score...
    assert poisoned.adjustments  # ...but never from the record


# --- the governed never-score list drives the code screen, in both directions -----------


def test_code_screen_follows_the_governed_topic_list():
    gap_adj = _adjustment(rationale="no employment gaps in the last 5 years")
    # Topic ON the list -> fires; topic removed from the list -> silent.
    assert "employment_gaps" in code_screen(gap_adj, _topics("employment_gaps"))
    assert code_screen(gap_adj, _topics("salary_history")) == []
    # Demographic language is a governed topic too now — removable like any
    # other (with the floor acknowledgment); off the list = silent.
    leak = _adjustment(rationale="as a female candidate in sales...")
    assert "demographic_language" in code_screen(leak, _topics("demographic_language"))
    assert code_screen(leak, []) == []


def test_stages_gate_the_code_screen():
    # In the v1 seed (default_scoring_model) demographic_language is enforced at
    # scoring + narrative only; v5 (2026-10-16) stages it for the checklist and
    # preferences too, through the ordinary governed path.
    topic = _topics("demographic_language")[0]
    assert "scoring" in topic.stages and "checklist" not in topic.stages


# --- narrative-stage screening ----------------------------------------------------------


def test_narrative_flags_fire_on_narrative_stage_topics():
    flags = narrative_flags("A great culture fit for the team.", TOPICS)
    assert flags and "Culture fit / vibes" in flags[0]
    assert narrative_flags("Strong GST filing record across FY24.", TOPICS) == []


def test_narrative_screen_runs_even_without_adjustments(tmp_path):
    client = FakeClient(CriticDraft(verdicts=[], overall_flags=[]))
    review, _ = run_critic(
        Layer2Result(candidate_id="c", adjustments=[],
                     narrative="would be a great culture fit"),
        [], TOPICS, client, "gpt-test", JsonCache(tmp_path))
    assert client.calls == 0  # still no model call
    assert any("Culture fit" in f for f in review.overall_flags)


# --- verdict merging mechanics -----------------------------------------------------------


def test_model_suspend_honored_and_missing_verdict_becomes_flag(tmp_path):
    result = Layer2Result(
        candidate_id="cid2",
        adjustments=[
            _adjustment(sid="skill_recency", rationale="recent GST work"),
            _adjustment(sid="hands_on_currency", rationale="still does the work"),
        ],
        narrative="n",
    )
    model = FakeClient(CriticDraft(
        verdicts=[
            AdjustmentVerdictDraft(signal_id="skill_recency", verdict="suspend", note="evidence does not support"),
            AdjustmentVerdictDraft(signal_id="not_an_adjusted_signal", verdict="suspend", note="ignored"),
            # hands_on_currency verdict intentionally missing
        ],
        overall_flags=["uniformly generous"],
    ))
    review, _ = run_critic(result, [_signal("skill_recency"), _signal("hands_on_currency")], [],
                           model, "gpt-test", JsonCache(tmp_path))
    assert review.verdict_per_adjustment == {"skill_recency": "suspend", "hands_on_currency": "flag"}
    assert any("no verdict" in n for n in review.notes)
    assert review.overall_flags == ["uniformly generous"]

    surviving, suspended = apply_critic(result, review)
    assert [a.signal_id for a in surviving] == ["hands_on_currency"]  # flag stays in score
    assert [a.signal_id for a in suspended] == ["skill_recency"]


def test_no_adjustments_skips_the_model_entirely(tmp_path):
    client = FakeClient(CriticDraft(verdicts=[], overall_flags=[]))
    review, cached = run_critic(Layer2Result(candidate_id="c", adjustments=[], narrative=""),
                                [], [], client, "gpt-test", JsonCache(tmp_path))
    assert client.calls == 0  # cost model: no adjustments -> no critic call
    assert review.verdict_per_adjustment == {} and not cached


def test_critic_result_is_cached_and_topic_edits_bust_the_cache(tmp_path):
    result = Layer2Result(candidate_id="c", adjustments=[_adjustment()], narrative="n")
    client = FakeClient(CriticDraft(
        verdicts=[AdjustmentVerdictDraft(signal_id="tenure_pattern_vs_stability", verdict="pass", note=None)],
        overall_flags=[],
    ))
    cache = JsonCache(tmp_path)
    topics = _topics("employment_gaps")
    first, cached1 = run_critic(result, [_signal("tenure_pattern_vs_stability")], topics, client, "gpt-test", cache)
    second, cached2 = run_critic(result, [_signal("tenure_pattern_vs_stability")], topics, client, "gpt-test", cache)
    assert client.calls == 1 and not cached1 and cached2 and first == second
    # a phrase edit re-audits: the full topic dump keys the cache
    edited = [topics[0].model_copy(update={"phrases": topics[0].phrases + ["résumé gap"]})]
    _, cached3 = run_critic(result, [_signal("tenure_pattern_vs_stability")], edited, client, "gpt-test", cache)
    assert client.calls == 2 and not cached3


# --- monitors stub -----------------------------------------------------------------------


def test_four_fifths_stub_is_honest_about_not_computing():
    results = four_fifths_monitor(["gender (voluntary self-disclosure)", "disability (voluntary self-disclosure)"])
    assert len(results) == 2
    assert all(m.passes_four_fifths is None for m in results)
    assert all("Not computed" in m.note for m in results)
    assert four_fifths_monitor([]) == []
