"""Dynamic copy + governance reads: /api/scoring-config serves the values every
on-screen scoring fact renders from (no literals in JSX); /api/scoring-model
serves knobs + metadata + signals + topics + the version history rail; the
per-version read powers "Restore this version"."""

from __future__ import annotations

from resume_screener import change_requests as cr
from resume_screener.scoring_model import canonical_dump, load_scoring_model
from tests.test_staleness import _as, client, workspace  # noqa: F401


def _payload(**edits) -> dict:
    data = canonical_dump(load_scoring_model())
    for path, value in edits.items():
        node = data
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return {"model": data}


def test_scoring_config_values_and_resolved_display(workspace, client):
    data = client.get("/api/scoring-config", headers=_as("TASpecialist")).json()
    assert data["version"] == 1
    assert data["bands"] == {"strong_at": 88, "possible_at": 60,
                             "cap_if_must_have_missing": "Possible"}
    assert data["clamp"] == {"min": 0, "max": 100}
    assert data["preferences"] == {"per_preference_cap_points": 2, "review_months": 6}
    assert data["adjustments"] == {"total_cap_points": 8}
    # templates resolved server-side — the UI never interpolates its own numbers
    assert data["display"]["preference_cap_note"] == (
        "Each preference can move a score by at most 2 points and never hides anyone.")
    assert "6 months" in data["display"]["review_note"]


def test_scoring_config_tracks_publishes_with_authors(workspace, client):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(preferences__per_preference_cap_points=3),
                            rationale="pilot feedback", author="hr@x")
    cr.approve_change(rec["change_id"], approver="legal@x")
    data = client.get("/api/scoring-config", headers=_as("TASpecialist")).json()
    assert data["version"] == 2
    assert data["published_by"] == "hr@x" and data["approved_by"] == "legal@x"
    assert "at most 3 points" in data["display"]["preference_cap_note"]


def test_scoring_config_gating(workspace, client):
    # recruiters and HMs read it (it feeds recruiter-facing copy)…
    assert client.get("/api/scoring-config", headers=_as("TASpecialist")).status_code == 200
    # …the SuperAdmin is the superset (2026-07-19): recruiter included → 200.
    assert client.get("/api/scoring-config", headers=_as("SuperAdmin")).status_code == 200


def test_scoring_model_read_knobs_metadata_and_versions(workspace, client):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    cr.approve_change(rec["change_id"], approver="admin@x")
    data = client.get("/api/scoring-model", headers=_as("SuperAdmin")).json()
    assert data["version"] == 2
    strong = next(k for k in data["knobs"] if k["path"] == "bands.strong_at")
    assert strong["value"] == 85
    assert strong["usual_bounds"] == [75, 95] and strong["label"]
    # the merged registry rides along: signals + topics, full shapes
    assert len(data["signals"]) == 12
    quota = next(s for s in data["signals"] if s["id"] == "quota_attainment_evidence")
    assert quota["display_name"] == "Quota attainment (sales)" and quota["cap_points"] == 5
    gaps = next(t for t in data["topics"] if t["id"] == "employment_gaps")
    assert gaps["floor"] is True and "employment gap" in gaps["phrases"]
    assert gaps["stages"] == ["scoring", "checklist", "preferences"]
    v2 = data["versions"][0]
    assert (v2["version"], v2["author"], v2["approved_by"]) == (2, "hr@x", "admin@x")
    assert v2["rationale"] == "calibration"
    # rules_view-gated: a TA Specialist can't read the governance surface
    assert client.get("/api/scoring-model", headers=_as("TASpecialist")).status_code == 403


def test_scoring_model_version_read_for_restore(workspace, client):
    rec = cr.propose_change("scoring_model", "scoring_model",
                            _payload(bands__strong_at=85),
                            rationale="calibration", author="hr@x")
    cr.approve_change(rec["change_id"], approver="admin@x")
    v1 = client.get("/api/scoring-model/versions/1", headers=_as("SuperAdmin")).json()
    assert v1["version"] == 1 and v1["model"]["bands"]["strong_at"] == 88
    assert len(v1["model"]["signals"]) == 12  # the full document, restorable
    assert client.get("/api/scoring-model/versions/9",
                      headers=_as("SuperAdmin")).status_code == 404
