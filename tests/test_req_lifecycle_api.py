"""P29: requisition lifecycle — close/reopen/snooze/events routes, duplicate-iCIMS
409, the close-suggestion truth table, retention math, and the governance
separation pin (req events never flip review_pending).

Offline: tmp_path workspaces + the TestClient/_as() pattern from
tests/test_sso_identity.py. All fixtures are synthetic — never real output/.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import load_req_meta, save_req_meta
from resume_screener.events import load_events
from tests.packdata import pack_days, plus_days
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A minimal project root: config.yaml + the committed policy packs + empty
    jds/, so routes and service helpers resolve everything under tmp_path."""
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _enable_flag(key: str) -> None:
    """Flip a feature flag on for the whole test workspace by editing features.yaml
    directly — deliberately NOT via feature_flags.set_flag, so no governance audit
    entry is created (these are lifecycle tests, not flag-publish tests)."""
    import yaml

    path = Path("signals/features.yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["features"][key]["default"] = True
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(
        timespec="seconds")


def _mk_role(stem="finance-analyst", title="Finance Analyst", location="IN", **meta):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text(f"# {title}\n\nOwns treasury accounting and audits.", encoding="utf-8")
    save_req_meta(jd, job_location=location, **meta)
    return stem, jd


def _mk_run(req_id, cids=("c1",), days_ago=20.0, run_id="run-1"):
    out = Path("output") / req_id
    (out / "candidates").mkdir(parents=True, exist_ok=True)
    for cid in cids:
        (out / "candidates" / f"{cid}.json").write_text(
            json.dumps({"score": {"candidate_id": cid, "fit_band": "Strong",
                                  "final_score": 82.0}}), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps({
        "run_id": run_id, "started_utc": _iso(days_ago), "policy_pack": "IN",
        "counts": {"scored": len(cids)},
        "versions": {"rubric": f"{req_id}.rubric.v2.yaml (v2)"},
    }), encoding="utf-8")


def _decide(cid, run_id="run-1", days_ago=20.0, reason="strong treasury depth",
            human_action="agree", final_band="Strong"):
    path = Path("output/feedback/decisions.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "candidate_id": cid, "run_id": run_id, "suggestion": "Strong (82)",
            "human_action": human_action, "final_band": final_band, "reason": reason,
            "reviewer": "ta@example.com", "versions": {}, "ts": _iso(days_ago),
        }) + "\n")


# --- close / reopen / snooze / FE events flows ---------------------------------------


def test_close_flow_stamps_sidecar_and_event(client):
    stem, jd = _mk_role()
    response = client.post(f"/api/roles/{stem}/close",
                           json={"outcome": "hired", "hired_count": 2},
                           headers=_as("HRHead"))
    assert response.status_code == 200
    assert response.json()["outcome"] == {"kind": "hired", "hired_count": 2}
    meta = load_req_meta(jd)
    assert meta["status"] == "closed"
    assert meta["closed_by"] == "hrhead@example.com"
    assert meta["closed_at"]
    [event] = load_events(req_id=stem, kinds=["req_close"])
    assert event["actor"] == "hrhead@example.com"
    assert "hired 2" in event["summary"]
    # closing twice is a 400, not a silent overwrite
    again = client.post(f"/api/roles/{stem}/close", json={"outcome": "cancelled"},
                        headers=_as("HRHead"))
    assert again.status_code == 400
    assert "already closed" in again.json()["detail"]


def test_close_requires_a_valid_outcome(client):
    stem, _jd = _mk_role()
    response = client.post(f"/api/roles/{stem}/close", json={"outcome": "maybe"},
                           headers=_as("HRHead"))
    assert response.status_code == 400
    assert "hired" in response.json()["detail"]


def test_closed_role_card_gains_retention_and_history_count(client):
    stem, _jd = _mk_role()
    client.post(f"/api/roles/{stem}/close", json={"outcome": "cancelled"},
                headers=_as("HRHead"))
    payload = client.get("/api/roles", headers=_as("TASpecialist")).json()
    assert payload["metrics"]["history_count"] == 1
    assert payload["metrics"]["awaiting_hm"] == 0
    [card] = payload["roles"]
    assert card["lifecycle"]["status"] == "closed"
    # the IN pack's two duties (audit-keep, PII-purge) — read from the pack, not pinned
    assert (card["retention"]["keep_at_least_days"], card["retention"]["purge_by_days"]) == pack_days("IN")
    assert card["region"] == "IN"
    assert card["hm"] is None


def test_reopen_flow(client):
    stem, jd = _mk_role()
    _enable_flag("icims_reopen")  # P37: reopen is flag-gated (default off)
    # reopening an open req is a 400
    assert client.post(f"/api/roles/{stem}/reopen",
                       headers=_as("HRHead")).status_code == 400
    client.post(f"/api/roles/{stem}/close", json={"outcome": "cancelled"},
                headers=_as("HRHead"))
    response = client.post(f"/api/roles/{stem}/reopen", headers=_as("HRHead"))
    assert response.status_code == 200
    meta = load_req_meta(jd)
    assert meta["status"] == "active"
    assert meta["outcome"] is None and meta["closed_at"] is None
    assert load_events(req_id=stem, kinds=["req_reopen"])


def test_snooze_defaults_to_30_days(client):
    stem, jd = _mk_role()
    response = client.post(f"/api/roles/{stem}/snooze", json={},
                           headers=_as("TASpecialist"))
    assert response.status_code == 200
    until = datetime.fromisoformat(load_req_meta(jd)["snooze_until"])
    assert until > datetime.now(timezone.utc) + timedelta(days=29)
    [event] = load_events(req_id=stem, kinds=["req_snooze"])
    assert "30 days" in event["summary"]


def test_snooze_rejects_bad_days(client):
    stem, _jd = _mk_role()
    for days in (0, "soon"):
        response = client.post(f"/api/roles/{stem}/snooze", json={"days": days},
                               headers=_as("TASpecialist"))
        assert response.status_code == 400


def test_fe_events_route_is_allowlisted(client):
    stem, _jd = _mk_role()
    ok = client.post(f"/api/roles/{stem}/events",
                     json={"action": "req_history_view"}, headers=_as("HRSpecialist"))
    assert ok.status_code == 200
    [event] = load_events(req_id=stem, kinds=["req_history_view"])
    assert event["actor"] == "hrspecialist@example.com"
    bad = client.post(f"/api/roles/{stem}/events",
                      json={"action": "made_up_event"}, headers=_as("HRSpecialist"))
    assert bad.status_code == 400
    assert load_events(req_id=stem, kinds=["made_up_event"]) == []


def test_lifecycle_routes_404_unknown_role(client):
    assert client.post("/api/roles/nope/close", json={"outcome": "cancelled"},
                       headers=_as("HRHead")).status_code == 404


def test_lifecycle_routes_403_without_a_role(client):
    from tests.test_sso_identity import principal

    stem, _jd = _mk_role()
    headers = {"X-MS-CLIENT-PRINCIPAL": principal("new@example.com", [])}
    for method, path, body in (
        ("post", f"/api/roles/{stem}/close", {"outcome": "hired"}),
        ("post", f"/api/roles/{stem}/reopen", None),
        ("post", f"/api/roles/{stem}/snooze", {}),
        ("post", f"/api/roles/{stem}/events", {"action": "req_history_view"}),
        ("get", f"/api/roles/{stem}/trail", None),
        ("get", f"/api/roles/{stem}/quickview", None),
    ):
        call = getattr(client, method)
        response = call(path, json=body, headers=headers) if body is not None \
            else call(path, headers=headers)
        assert response.status_code == 403, path


# --- create: sidecar stamping + duplicate-iCIMS 409 ----------------------------------


def test_create_role_stamps_the_sidecar(client):
    response = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Treasury Analyst", "jd_text": "Owns cash and banking.",
        "job_location": "SG", "icims_req_id": "IN-2026-1042",
    })
    assert response.status_code == 200
    meta = load_req_meta(Path("jds") / "treasury-analyst.md")
    assert meta["icims_req_id"] == "IN-2026-1042"
    assert meta["job_location"] == "SG"
    assert meta["status"] == "active" and meta["outcome"] is None
    assert meta["created_by"] == "taspecialist@example.com"
    assert meta["created_at"]
    assert meta["jd_language"] is None  # an English JD
    assert meta["hm"] is None
    # and the card reflects it
    [card] = client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]
    assert card["req_code"] == "IN-2026-1042"
    assert card["created_by"] == "taspecialist@example.com"
    assert card["region"] == "APAC"


def test_duplicate_icims_req_id_is_a_409(client):
    first = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Role One", "jd_text": "body", "icims_req_id": "IN-77",
        "job_location": "IN"})
    assert first.status_code == 200
    clash = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Role Two", "jd_text": "body", "icims_req_id": "IN-77",
        "job_location": "IN"})
    assert clash.status_code == 409
    # Structured detail: the form pins the message to the field AND deep-links
    # to the requisition already holding this id.
    detail = clash.json()["detail"]
    assert "IN-77" in detail["message"]
    assert detail["field"] == "icims_req_id"
    assert detail["req_id"] == "role-one"
    assert detail["title"] == "Role One"
    # a different id is fine
    assert client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Role Two", "jd_text": "body", "icims_req_id": "IN-78",
        "job_location": "IN"}).status_code == 200


def test_create_requires_the_icims_req_id(client):
    """P32: the iCIMS Req ID is the mapping anchor — creation without one is a
    400 with the exact message the form shows inline."""
    for payload in (
        {"title": "Role Three", "jd_text": "body", "job_location": "IN"},
        {"title": "Role Three", "jd_text": "body", "icims_req_id": "   ",
         "job_location": "IN"},
    ):
        response = client.post("/api/roles", headers=_as("TASpecialist"), json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == (
            "iCIMS Req ID is required — no mapping to the requisition is "
            "possible without it.")
    assert not (Path("jds") / "role-three.md").exists()


# --- /api/locations (P32: the New-requisition picker feed) ----------------------------


def test_locations_feed_shape_and_nothing_preselected(client):
    from tests.test_sso_identity import principal

    feed = client.get("/api/locations", headers=_as("TASpecialist")).json()
    packs = feed["packs"]
    assert len(packs) >= 7
    # 2026-09-06: + the pack's plain-language duties (obligations/notes/monitoring)
    # for the "what are these rules?" popover — additive, the five originals stay.
    assert all({"code", "label", "law", "keep_at_least_days", "purge_by_days",
                "obligations", "notes", "monitoring_categories"} <= set(loc)
               for loc in packs)
    assert all(isinstance(loc["obligations"], list) for loc in packs)
    # ALPHABETICAL, not pilot-first. The India pack used to sort to the top so a
    # fresh picker landed on it, which is how a recruiter in Budapest created an
    # India requisition by not touching the dropdown (2026-09-12).
    assert [p["label"] for p in packs] == sorted(p["label"] for p in packs)
    codes = {loc["code"] for loc in packs}
    assert {"IN", "SG", "JP", "EU", "US-default", "US-NYC", "US-CA"} <= codes
    india = next(p for p in packs if p["code"] == "IN")
    assert (india["keep_at_least_days"], india["purge_by_days"]) == pack_days("IN")
    assert "DPDP" in india["law"]
    # recruiter-guarded: a role-less user gets the friendly 403
    headers = {"X-MS-CLIENT-PRINCIPAL": principal("new@example.com", [])}
    assert client.get("/api/locations", headers=headers).status_code == 403


def test_locations_feed_carries_countries_and_us_states(client):
    """The form asks for a country; the pack is a lookup. Hungary has to be in
    the list, and a country with no pack has to be IN it — saying "no rules for
    Canada yet" is an answer, and leaving it out is not."""
    feed = client.get("/api/locations", headers=_as("TASpecialist")).json()
    by_code = {c["code"]: c for c in feed["countries"]}
    assert by_code["HU"]["name"] == "Hungary" and by_code["HU"]["pack"] == "EU"
    assert by_code["IN"]["pack"] == "IN"
    assert by_code["CA"]["pack"] is None  # Canada, and it says so
    states = {s["code"]: s for s in feed["us_states"]}
    assert states["CA"]["pack"] == "US-CA" and states["CA"]["name"] == "California"
    assert states["TX"]["pack"] == "US-default"
    assert states["NY"]["pack"] == "US-default"  # LL144 is a CITY law
    assert feed["nyc_pack"] == "US-NYC"


def test_locations_feed_states_where_data_is_processed_only_when_told(client, monkeypatch):
    assert client.get("/api/locations", headers=_as("TASpecialist")
                      ).json()["processing_locations"] is None
    monkeypatch.setenv("PROCESSING_LOCATIONS", "Files and database: Central India")
    assert client.get("/api/locations", headers=_as("TASpecialist")
                      ).json()["processing_locations"] == "Files and database: Central India"


def test_role_cards_carry_law_and_tz(client):
    _mk_role(location="US-CA")
    [card] = client.get("/api/roles", headers=_as("TASpecialist")).json()["roles"]
    assert card["law"]  # the pack-name subline
    assert card["tz"] == "America/Los_Angeles"
    assert card["region"] == "AMER"


def test_jd_language_heuristic():
    from resume_screener.api import service

    assert service.jd_language("Own the treasury close and GST filings.") is None
    assert service.jd_language("लेखा अनुभव पाँच वर्ष। कोषागार लेखांकन का स्वामित्व।") == "non_english"
    assert service.jd_language("1234 --- !!!") is None  # no letters at all


# --- quickview -----------------------------------------------------------------------


def test_quickview_shape(client):
    stem, _jd = _mk_role(created_by="taspecialist@example.com",
                         icims_req_id="IN-9")
    view = client.get(f"/api/roles/{stem}/quickview", headers=_as("TASpecialist")).json()
    assert "treasury accounting" in view["jd_excerpt"]
    assert "#" not in view["jd_excerpt"]  # heading stripped
    assert view["checklist_version"] is None and view["requirement_count"] == 0
    assert view["screened_count"] == 0
    assert view["added_by"] == "taspecialist@example.com"
    assert view["icims_req_id"] == "IN-9"


# --- close_suggestion truth table -----------------------------------------------------


def _suggestion(stem):
    from resume_screener.api import service

    return service.close_suggestion(service.find_requisition(stem))


def test_suggests_when_decided_and_quiet(workspace):
    stem, _jd = _mk_role()
    _mk_run(stem, cids=("c1", "c2"), days_ago=30)
    _decide("c1", days_ago=21)
    _decide("c2", days_ago=20)
    suggestion = _suggestion(stem)
    assert suggestion == {"suggest": True, "decided": 2, "quiet_days": 20}


def test_no_suggestion_without_a_run(workspace):
    stem, _jd = _mk_role(created_at=_iso(days_ago=40))
    assert _suggestion(stem)["suggest"] is False


def test_no_suggestion_while_candidates_are_undecided(workspace):
    stem, _jd = _mk_role()
    _mk_run(stem, cids=("c1", "c2"), days_ago=30)
    _decide("c1", days_ago=20)
    suggestion = _suggestion(stem)
    assert suggestion["suggest"] is False and suggestion["decided"] == 1


def test_no_suggestion_when_recently_active(workspace):
    stem, _jd = _mk_role()
    _mk_run(stem, days_ago=30)
    _decide("c1", days_ago=2)  # a fresh decision resets the quiet clock
    suggestion = _suggestion(stem)
    assert suggestion["suggest"] is False and suggestion["quiet_days"] == 2


def test_cleared_decision_counts_as_undecided(workspace):
    stem, _jd = _mk_role()
    _mk_run(stem, days_ago=30)
    # A clear is rerank with no final band (P34) — the reason is just prose.
    _decide("c1", days_ago=20, reason="Cleared — returned to undecided",
            human_action="rerank", final_band=None)
    assert _suggestion(stem)["suggest"] is False


def test_snooze_pauses_then_expires(workspace):
    stem, jd = _mk_role()
    _mk_run(stem, days_ago=30)
    _decide("c1", days_ago=20)
    save_req_meta(jd, snooze_until=_iso(days_ago=-5))  # five days from now
    assert _suggestion(stem)["suggest"] is False
    save_req_meta(jd, snooze_until=_iso(days_ago=1))  # expired yesterday
    assert _suggestion(stem)["suggest"] is True


def test_closed_req_never_suggests(workspace):
    stem, jd = _mk_role()
    _mk_run(stem, days_ago=30)
    _decide("c1", days_ago=20)
    save_req_meta(jd, status="closed")
    assert _suggestion(stem)["suggest"] is False


# --- retention_for math ---------------------------------------------------------------


def test_retention_for_closed_role(workspace):
    from resume_screener.api import service

    stem, jd = _mk_role(location="IN")
    save_req_meta(jd, status="closed", closed_at="2026-01-01T00:00:00+00:00")
    retention = service.retention_for(service.find_requisition(stem))
    keep, purge = pack_days("IN")
    assert retention == {"keep_at_least_days": keep, "purge_by_days": purge,
                         "purge_by": plus_days("2026-01-01T00:00:00+00:00", purge), "purged": False}


def test_retention_for_open_role_has_no_purge_date(workspace):
    from resume_screener.api import service

    stem, _jd = _mk_role(location="IN")
    retention = service.retention_for(service.find_requisition(stem))
    keep, purge = pack_days("IN")
    assert retention == {"keep_at_least_days": keep, "purge_by_days": purge,
                         "purge_by": None, "purged": False}


# --- governance separation: req events NEVER flip review_pending ----------------------


def test_req_events_never_flip_governance_review_pending(client):
    from resume_screener import governance
    from resume_screener.api import service

    stem, _jd = _mk_role()
    _enable_flag("icims_reopen")  # P37: so the reopen below actually runs
    assert governance.review_pending() is True  # never reviewed yet
    governance.record_review("HR + Legal", "2026-07-12", author="head@example.com")
    assert governance.review_pending() is False

    client.post(f"/api/roles/{stem}/close", json={"outcome": "cancelled"},
                headers=_as("HRHead"))
    client.post(f"/api/roles/{stem}/reopen", headers=_as("HRHead"))
    client.post(f"/api/roles/{stem}/snooze", json={}, headers=_as("HRHead"))
    client.post(f"/api/roles/{stem}/events", json={"action": "req_history_view"},
                headers=_as("HRHead"))
    service.record_req_event(stem, "ta@example.com", "req_snooze", "s", rationale="r")

    assert governance.review_pending() is False
    # the governance audit log holds ONLY the sign-off — no req/app events leaked in
    assert [e["action"] for e in governance.load_audit()] == ["review_signoff"]
    # and the events landed on the spine, not the audit log
    assert len(load_events(req_id=stem)) == 5
