"""Findings verification tests: the 'evidence or nothing' guardrail is code,
not model trust. Offline (fake client)."""

from types import SimpleNamespace

from resume_screener.cache import JsonCache
from resume_screener.findings import (
    FindingDraft,
    FindingsDraft,
    compute_findings,
    verify_findings,
)
from resume_screener.models import Requirement, Rubric

RUBRIC = Rubric(
    role_title="Role",
    jd_ref="jd.md",
    version=2,
    requirements=[
        Requirement(id="r01", text="US GAAP reporting", kind="must_have", weight=1),
        Requirement(id="r02", text="SOX controls", kind="must_have", weight=1),
        Requirement(id="r03", text="Oracle EBS", kind="nice_to_have", weight=1),
        Requirement(id="r04", text="CA qualification stated", kind="hard_gate", weight=0),
    ],
)

TEXT = "Prepared US GAAP\nreporting packs and supported SOX walkthroughs at [CANDIDATE] Corp."

DRAFTS = [
    # whitespace-variant quote (space vs newline) — must verify via normalization
    FindingDraft(requirement_id="r01", match="full", evidence="US GAAP reporting packs", note=None),
    # partial WITHOUT evidence — must be downgraded to none
    FindingDraft(requirement_id="r02", match="partial", evidence=None, note="close enough"),
    # fabricated quote — not in text — must be downgraded to none
    FindingDraft(requirement_id="r03", match="full", evidence="Oracle EBS power user", note=None),
    # unknown requirement id — must be dropped
    FindingDraft(requirement_id="r99", match="full", evidence="US GAAP", note=None),
    # r04 (the hard gate) intentionally missing — must be filled in as none
]


def test_verification_rules():
    findings = verify_findings(DRAFTS, RUBRIC, TEXT)
    by_id = {f.requirement_id: f for f in findings}

    assert set(by_id) == {"r01", "r02", "r03", "r04"}  # r99 dropped, gate filled
    assert [f.requirement_id for f in findings] == ["r01", "r02", "r03", "r04"]  # rubric order

    assert by_id["r01"].match == "full"
    assert by_id["r01"].evidence == "US GAAP reporting packs"

    # The "Downgraded" prefix is load-bearing — the confidence rating counts it —
    # and the rest of the note is recruiter language (it surfaces in the drawer).
    assert by_id["r02"].match == "none" and by_id["r02"].evidence is None
    assert by_id["r02"].note.split(" | ")[-1].startswith("Downgraded:")
    assert "quoted nothing" in by_id["r02"].note
    assert "close enough" in by_id["r02"].note  # model's note preserved

    assert by_id["r03"].match == "none" and by_id["r03"].evidence is None
    assert by_id["r03"].note.startswith("Downgraded:")
    assert "isn't in the résumé word for word" in by_id["r03"].note

    assert by_id["r04"].match == "none"
    assert "no finding" in by_id["r04"].note


def test_none_with_evidence_gets_evidence_stripped():
    drafts = [FindingDraft(requirement_id="r01", match="none", evidence="US GAAP", note=None)]
    findings = verify_findings(drafts, RUBRIC, TEXT)
    assert findings[0].match == "none" and findings[0].evidence is None


class FakeClient:
    def __init__(self, parsed):
        self.calls = 0

        def parse(**kwargs):
            self.calls += 1
            message = SimpleNamespace(refusal=None, parsed=parsed)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


def test_compute_findings_caches_and_stamps_rubric_version(tmp_path):
    cache = JsonCache(tmp_path)
    client = FakeClient(FindingsDraft(findings=DRAFTS))

    record, cached = compute_findings(TEXT, "cid1", RUBRIC, client, "gpt-test", cache)
    assert not cached and client.calls == 1
    assert record.rubric_version == 2 and record.candidate_id == "cid1"
    assert len(record.findings) == 4

    record2, cached2 = compute_findings(TEXT, "cid1", RUBRIC, client, "gpt-test", cache)
    assert cached2 and client.calls == 1 and record2 == record

    # editing the rubric (same version number, new text) must invalidate the cache
    edited = RUBRIC.model_copy(deep=True)
    edited.requirements[0].text = "IFRS reporting"
    _, cached3 = compute_findings(TEXT, "cid1", edited, client, "gpt-test", cache)
    assert not cached3 and client.calls == 2
