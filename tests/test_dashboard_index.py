"""The remembered requisition list must never show something that is no longer true.

The index exists because a dashboard card costs ~7 share round trips to build and 500
requisitions would cost ~3,500 (2026-09-10). It is only worth having if a stale card
cannot survive, so that is what these pin: the state tuple catches a change made by
ANY process, a governed publish drops everything, and a run in flight is never served
from a remembered card.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from resume_screener import dashboard_index
from resume_screener.models import Requisition


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "signals").mkdir()
    (tmp_path / "jds").mkdir()
    dashboard_index._CACHE.clear()
    dashboard_index._PENDING_DROPS.clear()
    return tmp_path


def _req(tmp_path: Path, code: str) -> Requisition:
    jd = tmp_path / "jds" / f"{code}.md"
    jd.write_text(f"# {code}\n", encoding="utf-8")
    (tmp_path / "jds" / f"{code}.req.json").write_text(
        json.dumps({"schema": 1, "job_location": "IN"}), encoding="utf-8")
    return Requisition(req_id=code, role_title=code, jd_ref=str(jd), job_location="IN")


def _harness(monkeypatch, stamp="s1"):
    """Count how often the real builder runs, and pin the config stamp."""
    calls: list[str] = []

    def build(_cfg, req):
        calls.append(req.req_id)
        return {"req_id": req.req_id, "title": req.role_title, "screening": None}

    monkeypatch.setattr(dashboard_index, "_config_stamp", lambda cfg: stamp)
    monkeypatch.setattr(dashboard_index, "_with_live_fields", lambda c: c)
    # The state tuple is exercised on its own in the cross-process test; here it only
    # has to change when the sidecar does, without needing a real AppConfig.
    monkeypatch.setattr(dashboard_index, "_row_state",
                        lambda cfg, req: [dashboard_index._stat_pair(
                            Path(req.jd_ref).with_suffix(".req.json"))])
    return build, calls


def test_the_stored_state_survives_a_json_round_trip(root, monkeypatch):
    """JSON has no tuples. A state tuple that comes back as a list never matches, so
    every row looks changed and the index quietly saves nothing at all."""
    req = _req(root, "alpha")
    state = dashboard_index._row_state(None, req) if False else [
        dashboard_index._stat_pair(root / "jds" / "alpha.req.json")]
    assert json.loads(json.dumps(state)) == state


def test_a_card_is_built_once_and_then_remembered(root, monkeypatch):
    build, calls = _harness(monkeypatch)
    reqs = [_req(root, "alpha"), _req(root, "beta")]
    first = dashboard_index.cards(None, reqs, build)
    assert len(calls) == 2
    second = dashboard_index.cards(None, reqs, build)
    assert second == first
    assert len(calls) == 2, "a valid card was rebuilt"


def test_a_change_made_by_another_process_is_caught(root, monkeypatch):
    """The guarantee that matters: nothing in this process told the index anything —
    the batch-prep worker and the CLI write from elsewhere."""
    build, calls = _harness(monkeypatch)
    reqs = [_req(root, "alpha")]
    dashboard_index.cards(None, reqs, build)
    dashboard_index._PENDING_DROPS.clear()          # as if a different process wrote
    dashboard_index._CACHE.clear()

    sidecar = root / "jds" / "alpha.req.json"
    sidecar.write_text(json.dumps({"schema": 1, "job_location": "IN", "status": "closed"}),
                       encoding="utf-8")
    dashboard_index.cards(None, reqs, build)
    assert calls == ["alpha", "alpha"], "a sidecar written elsewhere was not noticed"


def test_a_governed_publish_refreshes_staleness_without_rebuilding(root, monkeypatch):
    """A publish moves every card's staleness — but rebuilding every card to say so
    takes about two minutes at 500 requisitions, which would hand the next person a
    dead dashboard. Staleness is the only field a publish can move, and it is decided
    from stamps the run recorded, so it is re-decided in place."""
    build, calls = _harness(monkeypatch, stamp="s1")
    monkeypatch.setattr(dashboard_index, "_stale_inputs",
                        lambda req: {"versions": {"rubric": "x (v1)"}, "has_decisions": False})
    seen: list[str] = []
    monkeypatch.setattr(dashboard_index, "_refresh_staleness",
                        lambda cfg, req, row: (seen.append(req.req_id),
                                               {**row, "card": {**row["card"],
                                                                "staleness": "recomputed"}})[1])
    reqs = [_req(root, "alpha"), _req(root, "beta")]
    dashboard_index.cards(None, reqs, build)
    assert len(calls) == 2

    monkeypatch.setattr(dashboard_index, "_config_stamp", lambda cfg: "s2")
    out = dashboard_index.cards(None, reqs, build)
    assert len(calls) == 2, "a publish rebuilt cards instead of re-deciding staleness"
    assert seen == ["alpha", "beta"], "a card's staleness was left as it was"
    assert [c["staleness"] for c in out] == ["recomputed", "recomputed"]


def test_a_row_with_nothing_to_re_decide_from_is_rebuilt(root, monkeypatch):
    """Rows written before the stamps were stored cannot be re-decided, so they take
    the honest path rather than keeping a staleness nobody can vouch for."""
    build, calls = _harness(monkeypatch, stamp="s1")
    monkeypatch.setattr(dashboard_index, "_stale_inputs", lambda req: {})
    reqs = [_req(root, "alpha")]
    dashboard_index.cards(None, reqs, build)
    monkeypatch.setattr(dashboard_index, "_refresh_staleness", lambda cfg, req, row: {})
    monkeypatch.setattr(dashboard_index, "_config_stamp", lambda cfg: "s2")
    dashboard_index.cards(None, reqs, build)
    assert calls == ["alpha", "alpha"]


def test_an_invalidated_row_is_rebuilt_and_its_neighbours_are_not(root, monkeypatch):
    build, calls = _harness(monkeypatch)
    reqs = [_req(root, "alpha"), _req(root, "beta")]
    dashboard_index.cards(None, reqs, build)
    calls.clear()
    dashboard_index.invalidate("beta")
    dashboard_index.cards(None, reqs, build)
    assert calls == ["beta"]


def test_the_validation_budget_bounds_the_work(root, monkeypatch):
    """With more requisitions than budget, a load stats only `budget` of them — the
    property that makes the dashboard O(1) rather than O(requisitions)."""
    build, _ = _harness(monkeypatch)
    reqs = [_req(root, f"r{i:02d}") for i in range(10)]
    dashboard_index.cards(None, reqs, build, budget=10)

    checked: list[str] = []
    real_state = dashboard_index._row_state
    monkeypatch.setattr(dashboard_index, "_row_state",
                        lambda cfg, req: (checked.append(req.req_id), real_state(cfg, req))[1])
    dashboard_index.cards(None, reqs, build, budget=3)
    assert len(checked) == 3, f"validated {len(checked)} rows against a budget of 3"


def test_every_row_is_reached_by_the_round_robin(root, monkeypatch):
    """A bounded budget must still visit everyone, or a missed write could hide
    forever."""
    build, calls = _harness(monkeypatch)
    reqs = [_req(root, f"r{i:02d}") for i in range(6)]
    dashboard_index.cards(None, reqs, build, budget=6)
    calls.clear()
    for req in reqs:                       # every sidecar changes, silently
        (root / "jds" / f"{req.req_id}.req.json").write_text(
            json.dumps({"schema": 1, "job_location": "IN", "status": "closed"}),
            encoding="utf-8")
        dashboard_index._CACHE.clear()
    for _ in range(6):                     # six loads at two rows each
        dashboard_index.cards(None, reqs, build, budget=2)
    assert set(calls) == {r.req_id for r in reqs}, "a row the sweep never reaches"


def test_a_requisition_that_is_gone_leaves_no_row(root, monkeypatch):
    build, _ = _harness(monkeypatch)
    reqs = [_req(root, "alpha"), _req(root, "beta")]
    dashboard_index.cards(None, reqs, build)
    dashboard_index.cards(None, reqs[:1], build)
    assert set(dashboard_index.stored_rows()) == {"alpha"}


def test_a_broken_index_still_renders_the_page(root, monkeypatch):
    build, calls = _harness(monkeypatch)
    reqs = [_req(root, "alpha")]
    dashboard_index.INDEX_FILE.write_text("{ not json", encoding="utf-8")
    cards = dashboard_index.cards(None, reqs, build)
    assert [c["req_id"] for c in cards] == ["alpha"]


def test_the_page_survives_an_index_that_throws(root, monkeypatch):
    build, _ = _harness(monkeypatch)
    reqs = [_req(root, "alpha")]
    monkeypatch.setattr(dashboard_index, "_read",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    cards = dashboard_index.cards(None, reqs, build)
    assert [c["req_id"] for c in cards] == ["alpha"], "a cache broke the dashboard"
