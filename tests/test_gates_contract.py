"""P33: the single-list rubric contract. Normalization ignores gates, gate-fail
semantics, single-list YAML round-trip (and the retired two-list shape failing
LOUDLY — greenfield, no loader shim), and the checklist editor's PUT round-trip
preserving row order + kinds. Offline throughout."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from resume_screener.findings import FindingsRecord
from resume_screener.layer1 import passes_gate, score_layer1
from resume_screener.models import Finding, Requirement, Rubric
from resume_screener.rubric import RubricError, load_rubric, save_rubric
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


def _req(id_, text, kind, weight) -> Requirement:
    return Requirement(id=id_, text=text, kind=kind, weight=weight)


# --- normalization -------------------------------------------------------------------


def test_normalization_ignores_gates():
    # Whatever weight a gate row carries in the file, normalized() zeroes it and
    # scales ONLY the non-gate rows to sum 1.
    rubric = Rubric(role_title="R", jd_ref="jd.md", requirements=[
        _req("r01", "US GAAP", "must_have", 3),
        _req("r02", "Oracle EBS", "nice_to_have", 1),
        _req("r03", "CA qualification", "hard_gate", 5),  # hostile weight
    ]).normalized()
    weights = {r.id: r.weight for r in rubric.requirements}
    assert weights == {"r01": 0.75, "r02": 0.25, "r03": 0.0}


def test_normalization_uniform_over_non_gates_only():
    # All-zero non-gate weights -> uniform over the NON-GATE rows; gate stays 0.
    rubric = Rubric(role_title="R", jd_ref="jd.md", requirements=[
        _req("r01", "A", "must_have", 0),
        _req("r02", "B", "nice_to_have", 0),
        _req("r03", "Gate", "hard_gate", 0),
    ]).normalized()
    weights = {r.id: r.weight for r in rubric.requirements}
    assert weights == {"r01": 0.5, "r02": 0.5, "r03": 0.0}


# --- gate-fail semantics ----------------------------------------------------------------


def test_gate_full_passes_partial_and_none_fail():
    rubric = Rubric(role_title="R", jd_ref="jd.md", requirements=[
        _req("r01", "US GAAP", "must_have", 1),
        _req("r02", "CA qualification stated", "hard_gate", 0),
    ])

    def record(gate_match):
        return FindingsRecord(
            candidate_id="c", role_title="R", rubric_version=1,
            findings=[
                Finding(requirement_id="r01", requirement_text="US GAAP", match="full", evidence="q"),
                Finding(requirement_id="r02", requirement_text="CA qualification stated", match=gate_match,
                        evidence="q" if gate_match != "none" else None),
            ],
        )

    passed = score_layer1(record("full"), rubric)
    assert passed.eligible and passed.gate_flags == [] and passes_gate(passed)

    for miss in ("partial", "none"):
        failed = score_layer1(record(miss), rubric)
        assert failed.eligible is False
        assert failed.gate_flags == ["r02"]
        assert "Hard requirement not met: CA qualification stated" in failed.reasons
        assert failed.base_score == 100.0  # never hidden, still scored
        assert passes_gate(failed) is False


# --- YAML round-trip ------------------------------------------------------------------


def test_single_list_yaml_round_trip_preserves_order_and_kinds(tmp_path):
    jd = tmp_path / "role.md"
    jd.write_text("# Role", encoding="utf-8")
    rubric = Rubric(role_title="Role", jd_ref=str(jd), requirements=[
        _req("r01", "Gate first", "hard_gate", 0),
        _req("r02", "Must in the middle", "must_have", 4),
        _req("r03", "Nice at the end", "nice_to_have", 2),
    ])
    path = save_rubric(rubric, jd)
    loaded = load_rubric(path)
    assert loaded.version == 1
    assert [r.text for r in loaded.requirements] == [
        "Gate first", "Must in the middle", "Nice at the end"]
    assert [r.kind for r in loaded.requirements] == ["hard_gate", "must_have", "nice_to_have"]
    assert loaded.requirements[0].weight == 0.0
    assert abs(sum(r.weight for r in loaded.requirements) - 1.0) < 1e-3


def test_old_two_list_yaml_fails_loudly(tmp_path):
    # Greenfield: NO loader shim. A pre-P33 file with hard_filters: must fail
    # validation (extra key forbidden), never silently drop its gates.
    old = {
        "role_title": "Role", "jd_ref": "jd.md", "version": 1,
        "hard_filters": [{"id": "hf01", "text": "CA qualification"}],
        "requirements": [{"id": "r01", "text": "US GAAP", "type": "other",
                          "kind": "must_have", "weight": 1.0}],
    }
    f = tmp_path / "role.rubric.v1.yaml"
    f.write_text(yaml.safe_dump(old, sort_keys=False), encoding="utf-8")
    with pytest.raises(RubricError, match="hard_filters"):
        load_rubric(f)
    with pytest.raises(ValidationError):
        Rubric.model_validate(old)


# --- the checklist editor's PUT round-trip -------------------------------------------


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


ROWS = [
    {"text": "CA / CPA qualification", "kind": "hard_gate", "importance": 3},
    {"text": "Treasury accounting depth", "kind": "must_have", "importance": 5},
    {"text": "Oracle EBS exposure", "kind": "nice_to_have", "importance": 2},
]


def _mk_role(client) -> str:
    created = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "IN", "icims_req_id": "2026-9001",
    })
    assert created.status_code == 200, created.text
    return created.json()["req_id"]


def test_editor_put_round_trip_preserves_order_and_kinds(client):
    req_id = _mk_role(client)
    saved = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                       json={"requirements": ROWS})
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["version"] == 1
    assert [r["text"] for r in payload["requirements"]] == [row["text"] for row in ROWS]
    assert [r["kind"] for r in payload["requirements"]] == ["hard_gate", "must_have", "nice_to_have"]
    # a second save is the next version, same shape
    again = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                       json={"requirements": ROWS})
    assert again.json()["version"] == 2
    fetched = client.get(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist")).json()
    assert [r["kind"] for r in fetched["requirements"]] == ["hard_gate", "must_have", "nice_to_have"]


def test_editor_put_rejects_unknown_kind(client):
    req_id = _mk_role(client)
    bad = client.put(f"/api/roles/{req_id}/rubric", headers=_as("TASpecialist"),
                     json={"requirements": [{"text": "X", "kind": "hard_filter", "importance": 3}]})
    assert bad.status_code == 400
    assert "kind" in bad.json()["detail"]
