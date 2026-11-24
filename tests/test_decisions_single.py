"""B5: POST /api/roles/{req_id}/decisions returns the updated decision entry +
fresh decision_counts (and the batch route gains the counts too) so the Review
page reconciles a click in place instead of refetching the whole payload.

Offline: tmp_path workspace + the synthetic screened-role fixture from
tests/test_decisions_batch.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_decisions_batch import (  # noqa: F401 — fixtures resolve by name
    client,
    screened_role,
    workspace,
)
from tests.test_sso_identity import _as

FEEDBACK = Path("output/feedback/decisions.jsonl")


def _log_lines() -> list[dict]:
    if not FEEDBACK.exists():
        return []
    return [json.loads(line) for line in FEEDBACK.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_single_decision_returns_entry_and_counts(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    response = client.post(f"/api/roles/{req_id}/decisions", headers=_as("TASpecialist"),
                           json={"candidate_id": cids[0], "action": "forward",
                                 "reason": "Strong treasury depth"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["action"] == "forward"
    assert body["decision"]["candidate_id"] == cids[0]
    assert body["decision"]["action"] == "forward"
    assert body["decision"]["reason"] == "Strong treasury depth"
    assert body["decision"]["ts"]  # stamped by the server
    assert body["decision_counts"] == {"forward": 1, "discuss": 0, "not": 0,
                                       "awaiting": 2}


def test_clear_echoes_and_counts_drop(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    client.post(f"/api/roles/{req_id}/decisions", headers=_as("TASpecialist"),
                json={"candidate_id": cids[0], "action": "forward"})
    cleared = client.post(f"/api/roles/{req_id}/decisions", headers=_as("TASpecialist"),
                          json={"candidate_id": cids[0], "action": "clear"}).json()
    assert cleared["action"] == "clear"
    assert cleared["decision"]["action"] == "clear"
    assert cleared["decision_counts"] == {"forward": 0, "discuss": 0, "not": 0,
                                          "awaiting": 3}
    assert len(_log_lines()) == 2  # append-only: the clear is one MORE line


def test_noteless_decision_reason_is_empty_but_log_stores_the_label(client, screened_role):
    """The P34 pin: the log's reason field is required-non-empty, so a note-less
    decision stores the bare action label — but the API reports the display
    note, which is empty."""
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    body = client.post(f"/api/roles/{req_id}/decisions", headers=_as("TASpecialist"),
                       json={"candidate_id": cids[0], "action": "forward"}).json()
    assert body["decision"]["reason"] == ""
    [line] = _log_lines()
    assert line["reason"] == "Move forward"
    # and the review card shows no note either
    cards = client.get(f"/api/roles/{req_id}/review",
                       headers=_as("TASpecialist")).json()["cards"]
    decided = next(c for c in cards if c["candidate_id"] == cids[0])
    assert decided["decided"] == "forward" and decided["decided_reason"] == ""


def test_batch_response_carries_decision_counts(client, screened_role):
    req_id, cids = screened_role["req_id"], screened_role["cids"]
    body = client.post(f"/api/roles/{req_id}/decisions/batch", headers=_as("TASpecialist"),
                       json={"candidate_ids": cids[:2], "action": "discuss"}).json()
    assert body["decision_counts"] == {"forward": 0, "discuss": 2, "not": 0,
                                       "awaiting": 1}
