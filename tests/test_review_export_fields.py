"""B6: the Review payload carries what the TA's CSV export needs — per-card
must_have_gaps as requirement TEXTS (raw-id fallback when the checklist row was
edited away) and the root req_code (the iCIMS id). Offline: the synthetic
screened-role fixture from tests/test_decisions_batch.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_decisions_batch import (  # noqa: F401 — fixtures resolve by name
    _record,
    client,
    screened_role,
    workspace,
)
from tests.test_sso_identity import _as


def test_cards_carry_must_have_gap_texts_and_req_code(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    # give one candidate a must-have gap referencing the checklist row (r01)
    rec = _record(cids[0])
    rec["score"]["layer1"]["must_haves_missing"] = ["r01"]
    Path("output", req_id, "candidates", f"{cids[0]}.json").write_text(
        json.dumps(rec), encoding="utf-8")

    payload = client.get(f"/api/roles/{req_id}/review", headers=_as("TASpecialist")).json()
    assert payload["req_code"] == "2026-9010"
    by_id = {c["candidate_id"]: c for c in payload["cards"]}
    assert by_id[cids[0]]["must_have_gaps"] == ["US GAAP"]  # TEXT, not the id
    assert by_id[cids[1]]["must_have_gaps"] == []


def test_gap_for_an_edited_away_row_falls_back_to_the_id(client, screened_role):
    """A run can reference a requirement id the current checklist no longer has
    — show the raw id rather than hiding the gap."""
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    rec = _record(cids[0])
    rec["score"]["layer1"]["must_haves_missing"] = ["r99"]
    Path("output", req_id, "candidates", f"{cids[0]}.json").write_text(
        json.dumps(rec), encoding="utf-8")
    payload = client.get(f"/api/roles/{req_id}/review", headers=_as("TASpecialist")).json()
    by_id = {c["candidate_id"]: c for c in payload["cards"]}
    assert by_id[cids[0]]["must_have_gaps"] == ["r99"]
