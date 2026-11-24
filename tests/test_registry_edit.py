"""The registry view over the merged scoring model (2026-07-18): load_registry
derives the §4 SignalRegistry from signals/scoring_model.vN.yaml — same version,
signals verbatim, topics projected to the {id, reason} excluded list. Offline."""

import shutil
from pathlib import Path

import pytest

from resume_screener.registry import RegistryError, load_registry

SEED = Path("signals/scoring_model.v1.yaml")


def test_registry_derives_from_the_scoring_model(tmp_path):
    shutil.copy(SEED, tmp_path / "scoring_model.v1.yaml")
    registry = load_registry(tmp_path / "registry.yaml")  # legacy file anchor still works
    assert registry.version == 1
    by_id = {s.id: s for s in registry.signals}
    assert by_id["quota_attainment_evidence"].cap_points == 5
    assert by_id["public_work_relevance"].enabled_default is False
    assert len(registry.excluded) == 9
    excluded = {e.id: e.reason for e in registry.excluded}
    assert "Gaps are life" in excluded["employment_gaps"]


def test_registry_accepts_a_directory_anchor(tmp_path):
    shutil.copy(SEED, tmp_path / "scoring_model.v1.yaml")
    assert load_registry(tmp_path).version == 1


def test_new_model_version_is_the_new_registry(tmp_path):
    shutil.copy(SEED, tmp_path / "scoring_model.v1.yaml")
    v2 = (tmp_path / "scoring_model.v1.yaml").read_text(encoding="utf-8").replace(
        "version: 1", "version: 2", 1).replace("cap_points: 5", "cap_points: 4", 1)
    (tmp_path / "scoring_model.v2.yaml").write_text(v2, encoding="utf-8")
    registry = load_registry(tmp_path)
    assert registry.version == 2
    assert {s.id: s for s in registry.signals}["quota_attainment_evidence"].cap_points == 4


def test_broken_model_surfaces_a_plain_registry_error(tmp_path):
    (tmp_path / "scoring_model.v1.yaml").write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="not a valid scoring model"):
        load_registry(tmp_path)
