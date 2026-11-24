"""publish_scoring_model (full-draft form since the 2026-07-18 merge):
rationale required, per-version files (old versions immutable), schema rails,
version ownership, audit entry shape, and the review-pending reopen on direct
publishes."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from resume_screener import governance
from resume_screener.scoring_model import (
    ScoringModelError, canonical_dump, load_scoring_model,
    load_scoring_model_version, scoring_model_stamp,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "scoring_model.v1.yaml",
                    tmp_path / "signals" / "scoring_model.v1.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _draft(**edits) -> dict:
    data = canonical_dump(load_scoring_model())
    for path, value in edits.items():
        node = data
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return data


def test_publish_writes_next_version_file_and_audits(workspace):
    version = governance.publish_scoring_model(
        _draft(bands__strong_at=85, preferences__review_months=12),
        author="admin@x", rationale="calibration")
    assert version == 2
    assert Path("signals/scoring_model.v2.yaml").exists()
    assert Path("signals/scoring_model.v1.yaml").exists()  # never touched
    assert load_scoring_model_version(1).bands.strong_at == 88  # as-of intact

    sm = load_scoring_model()
    assert (sm.version, sm.bands.strong_at, sm.preferences.review_months) == (2, 85, 12)
    assert len(sm.signals) == 12 and len(sm.topics) == 9  # the merge rides along
    assert scoring_model_stamp().startswith("v2 (")

    entry = governance.load_audit()[-1]
    assert entry["action"] == "publish_scoring_model" and entry["version"] == 2
    assert "“Strong match” starts at: 88 → 85" in entry["summary"]
    assert "re-review window" in entry["summary"]
    assert entry["author"] == "admin@x" and entry["rationale"] == "calibration"


def test_rationale_and_change_required(workspace):
    with pytest.raises(ScoringModelError, match="reason for the change"):
        governance.publish_scoring_model(_draft(bands__strong_at=85),
                                         author="a@x", rationale=" ")
    with pytest.raises(ScoringModelError, match="Nothing changed"):
        governance.publish_scoring_model({}, author="a@x", rationale="r")
    with pytest.raises(ScoringModelError, match="Nothing changed"):
        governance.publish_scoring_model(_draft(), author="a@x", rationale="r")


def test_server_owns_the_version_number(workspace):
    draft = _draft(bands__strong_at=85)
    draft["version"] = 99  # a client can't skip versions
    version = governance.publish_scoring_model(draft, author="a@x", rationale="r")
    assert version == 2 and load_scoring_model().version == 2


def test_invalid_values_rejected_by_schema(workspace):
    with pytest.raises(ScoringModelError, match="Possible < Strong"):
        governance.publish_scoring_model(_draft(bands__strong_at=40),
                                         author="a@x", rationale="r")
    assert load_scoring_model().version == 1  # nothing landed


def test_publish_from_bare_root_starts_at_v1(tmp_path, monkeypatch):
    (tmp_path / "signals").mkdir()
    monkeypatch.chdir(tmp_path)
    version = governance.publish_scoring_model(_draft(bands__strong_at=90),
                                               author="a@x", rationale="r")
    assert version == 1
    sm = load_scoring_model()
    assert (sm.version, sm.bands.strong_at) == (1, 90)


def test_direct_publish_reopens_review_banner(workspace):
    governance.record_review("HR + Legal", "2026-07-01", author="hr@x")
    assert governance.review_pending() is False
    governance.publish_scoring_model(_draft(bands__strong_at=85),
                                     author="a@x", rationale="r")
    assert governance.review_pending() is True
