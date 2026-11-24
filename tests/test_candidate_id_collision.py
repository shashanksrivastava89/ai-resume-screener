"""Two résumés that redact alike are still two candidates (2026-10-14). Offline.

A candidate's id is the hash of their file's bytes, but the model caches are keyed on
the REDACTED text — so two byte-different files whose redacted text is identical (the
same person saved twice; two résumés that differ only in masked details) shared every
cached record, stamped with whoever came first. The second candidate's artifacts and
result file then overwrote the first's, and one of them vanished from the list
(found by the 6 Oct bias test). Now each keeps its own id, and identical inputs still
share ONE model judgement — no second call, no second dose of run-to-run noise.
Synthetic people only.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from resume_screener.cache import JsonCache, content_hash
from resume_screener.findings import FindingDraft, FindingsDraft, compute_findings, freeze_findings
from resume_screener.ingest import extract_file, freeze_profile, parse_profile
from resume_screener.layer1 import score_layer1
from resume_screener.layer2 import LAYER2_PROMPT_VERSION, Layer2Draft, run_layer2
from resume_screener.localextract import tiny_pdf
from resume_screener.models import Requirement, Rubric
from resume_screener.redact import redact_text
from resume_screener.report import CandidateBundle, write_reports
from resume_screener.scoring import assemble_score
from tests.test_extraction_routing import FULL_PAGE, FakeDI, HYBRID
from tests.test_layer2 import ENABLED, _fixtures
from tests.test_profile import DRAFT

RUBRIC = Rubric(role_title="Senior Accountant", jd_ref="jd.md", version=1, requirements=[
    Requirement(id="r01", text="Ind AS reporting", kind="must_have", weight=1),
])
FINDINGS = FindingsDraft(findings=[FindingDraft(
    requirement_id="r01", match="full",
    evidence="Prepared statutory financial statements under Ind AS", note=None)])


class Model:
    """Answers each structured call with the canned draft for its schema; counts calls."""

    def __init__(self, layer2=None):
        self.calls: list[str] = []
        answers = {"ProfileDraft": DRAFT, "FindingsDraft": FINDINGS,
                   "Layer2Draft": layer2 or Layer2Draft(adjustments=[], proposed_signals=[],
                                                        narrative="Steady close owner.")}

        def parse(**kwargs):
            name = kwargs["response_format"].__name__
            self.calls.append(name)
            message = SimpleNamespace(refusal=None, parsed=answers[name])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


def _two_lookalikes(tmp_path) -> list[tuple[str, Path, str]]:
    """(candidate id, file, redacted text) for two people whose résumés differ only in
    the name — which redaction masks."""
    cache = JsonCache(tmp_path / ".cache")
    out = []
    for name in ("Alice Alpha", "Bob Beta"):
        path = tmp_path / f"{name}.pdf"
        path.write_bytes(tiny_pdf([[name, *FULL_PAGE]]))
        ext = extract_file(FakeDI(), "prebuilt-layout", path, cache, HYBRID)
        out.append((ext.candidate_id, path, redact_text(ext.text, ext.candidate_id, path.name).redacted_text))
    return out


def test_lookalike_resumes_keep_their_own_ids_through_every_cached_stage(tmp_path):
    (id_a, path_a, text_a), (id_b, path_b, text_b) = _two_lookalikes(tmp_path)
    assert id_a != id_b and text_a == text_b  # different people, one model input

    cache, model = JsonCache(tmp_path / ".cache"), Model()
    artifacts = tmp_path / "artifacts"
    bundles = []
    for cid, path, text in ((id_a, path_a, text_a), (id_b, path_b, text_b)):
        profile, _ = parse_profile(text, cid, str(path), model, "gpt-test", cache)
        record, _ = compute_findings(text, cid, RUBRIC, model, "gpt-test", cache)
        assert (profile.candidate_id, profile.source_file, record.candidate_id) == (cid, str(path), cid)
        freeze_profile(profile, artifacts)
        freeze_findings(record, artifacts)
        layer1 = score_layer1(record, RUBRIC)
        score = assemble_score(layer1, None, None, role_title="Senior Accountant",
                               jurisdiction="IN", extraction_confidence=1.0, downgraded_findings=0)
        bundles.append(CandidateBundle(score=score, findings=record))

    assert model.calls == ["ProfileDraft", "FindingsDraft"]  # the second was all cache
    for cid in (id_a, id_b):
        assert (artifacts / f"{cid}.profile.json").exists()
        assert (artifacts / f"{cid}.findings.json").exists()

    out = tmp_path / "out"
    write_reports(out, bundles, RUBRIC, {"run_id": "run-1", "versions": {}})
    written = {json.loads(p.read_text(encoding="utf-8"))["score"]["candidate_id"]
               for p in (out / "candidates").glob("*.json")}
    assert written == {id_a, id_b}  # both on the list; neither overwrote the other


def test_lookalikes_share_one_layer2_judgement_each_under_its_own_id(tmp_path):
    profile_a, record_a, features_a = _fixtures()
    profile_b = profile_a.model_copy(update={"candidate_id": "cid-b", "source_file": "Other.pdf"})
    record_b = record_a.model_copy(update={"candidate_id": "cid-b"})
    features_b = features_a.model_copy(update={"candidate_id": "cid-b"})
    cache, model = JsonCache(tmp_path / "cache"), Model()
    common = dict(enabled_signals=ENABLED, excluded=[], jd_text="JD", total_cap=15,
                  jurisdiction="IN", client=model, model="gpt-test", cache=cache,
                  proposals_path=tmp_path / "proposals.jsonl")

    res_a, _, cached_a = run_layer2(profile_a, record_a, features_a, **common)
    res_b, _, cached_b = run_layer2(profile_b, record_b, features_b, **common)
    assert (cached_a, cached_b) == (False, True) and model.calls == ["Layer2Draft"]
    assert (res_a.candidate_id, res_b.candidate_id) == ("cid9", "cid-b")
    assert res_a.narrative == res_b.narrative


def test_a_layer2_entry_cached_before_this_change_is_still_served(tmp_path):
    """Warm caches stay warm: the old, id-bearing key is still found — so an existing
    requisition's re-screen does not re-ask the model and drift by its noise."""
    from resume_screener.layer2 import Layer2Result

    profile, record, features = _fixtures()
    cache, model = JsonCache(tmp_path / "cache"), Model()
    never_score: list = []
    signal_sheet = [{"id": s.id, "description": s.definition or s.description, "kind": s.kind,
                     "direction": s.direction, "cap_points": s.cap_points,
                     "job_relatedness": s.job_relatedness, "params": s.params} for s in ENABLED]
    legacy_key = content_hash(
        profile.redacted_text, json.dumps(signal_sheet, sort_keys=True),
        json.dumps(never_score, sort_keys=True), record.model_dump_json(),
        features.model_dump_json(), "JD", "15", "gpt-test", LAYER2_PROMPT_VERSION)
    cache.set("layer2", legacy_key, {
        "result": Layer2Result(candidate_id="cid9", adjustments=[], proposed_signals=[],
                               narrative="from before").model_dump(),
        "enforcement": []})

    res, _, cached = run_layer2(profile, record, features, ENABLED, [], jd_text="JD",
                                total_cap=15, jurisdiction="IN", client=model,
                                model="gpt-test", cache=cache,
                                proposals_path=tmp_path / "proposals.jsonl")
    assert cached and model.calls == [] and res.narrative == "from before"


def test_lookalikes_share_one_preference_answer(tmp_path, monkeypatch):
    from resume_screener import azure_clients, pref_match
    from tests.test_pref_match import _findings, _profile

    calls = []

    def fake(_client, _model, _messages, _schema):
        calls.append(1)
        return pref_match.PrefMatchList(matches=[pref_match.PrefMatchDraft(
            pref_id="p1", matched=True, points=2.0, evidence="GST and TDS filings",
            rationale="does GST")])

    monkeypatch.setattr(azure_clients, "parse_structured", fake)
    prefs = [{"pref_id": "p1", "owner": "o", "text": "prefers GST depth"}]
    cache = JsonCache(tmp_path)
    first, _ = pref_match.run_pref_match(_profile(), _findings(), prefs, client=None,
                                         model="m", cache=cache)
    other = _findings().model_copy(update={"candidate_id": "someone-else"})
    second, cached = pref_match.run_pref_match(
        _profile().model_copy(update={"candidate_id": "someone-else"}), other, prefs,
        client=None, model="m", cache=cache)
    assert cached and calls == [1] and second == first
