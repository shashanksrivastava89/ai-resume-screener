"""Decisions through the API with the verdict encrypted at rest (2026-10-14). Offline.

The repository seals; these pin what a person sees: a decision saved under a key
reads back with its note on Review, a key that is configured but unreadable refuses
the save in plain words (503, nothing recorded), and sharing with the hiring manager
no longer copies every note and reviewer address into the plain event log.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy.orm import sessionmaker

from resume_screener import feedback, fieldcrypto
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.models.runtime import Decision as DecisionRow
from resume_screener.events import load_events
from tests.dbharness import make_test_engine
from tests.test_hm_share import TA_EMAIL, _mk_screened_role, client, workspace  # noqa: F401
from tests.test_sso_identity import _as


@pytest.fixture
def db_decisions(monkeypatch):
    """FEEDBACK_BACKEND=db on a throwaway SQLite (only the decision log moves)."""
    eng = make_test_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(feedback, "_use_db", lambda: True)
    yield maker
    eng.dispose()


@pytest.fixture
def decision_key(monkeypatch):
    key = os.urandom(32)
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    fieldcrypto.set_key_provider(lambda version: ("k1", key))
    yield
    fieldcrypto.set_key_provider(None)


def test_a_sealed_decision_reads_back_with_its_note(client, db_decisions, decision_key):
    stem, ids = _mk_screened_role()
    r = client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": ids[0], "action": "forward",
                          "reason": "Strong treasury close experience"})
    assert r.status_code == 200, r.text

    with db_decisions() as s:
        [row] = s.query(DecisionRow).all()
        assert row.verdict_enc and row.suggestion is None and row.reason == ""
        assert row.req_code == stem  # the requisition now travels with a DB decision

    view = client.get(f"/api/roles/{stem}/review", headers=_as("TASpecialist")).json()
    card = next(c for c in view["cards"] if c["candidate_id"] == ids[0])
    assert card["decided"] == "forward"
    assert card["decided_reason"] == "Strong treasury close experience"


def test_an_unreadable_key_refuses_the_save_in_plain_words(client, db_decisions, monkeypatch):
    stem, ids = _mk_screened_role()
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")

    def unreachable(_version):
        raise fieldcrypto.DecisionKeyUnavailable("vault unreachable")

    fieldcrypto.set_key_provider(unreachable)
    r = client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": ids[0], "action": "forward", "reason": "x"})
    assert r.status_code == 503
    assert "couldn't be read" in r.json()["detail"] and "Nothing was recorded" in r.json()["detail"]
    with db_decisions() as s:
        assert s.query(DecisionRow).count() == 0


def test_the_share_event_names_no_note_or_reviewer_but_the_hm_still_reads_both(client):
    stem, ids = _mk_screened_role()
    r = client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                    json={"candidate_id": ids[0], "action": "forward",
                          "reason": "Closed the books at a listed company"})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/roles/{stem}/share-hm",
                       headers=_as("TASpecialist")).status_code == 200

    [event] = load_events(req_id=stem, kinds=["shortlist_shared"])
    stored = json.dumps(event["data"]["snapshot"])
    assert "Closed the books" not in stored and TA_EMAIL not in stored
    assert all(set(row) == {"candidate_id", "action", "ts"} for row in event["data"]["snapshot"])

    hm = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    card = next(c for c in hm["cards"] if c["candidate_id"] == ids[0])
    assert card["decided_reason"] == "Closed the books at a listed company"
    assert card["decided_by"] == TA_EMAIL
    detail = client.get(f"/api/roles/{stem}/review/{ids[0]}",
                        headers=_as("HiringManager")).json()
    assert detail["decision"]["reason"] == "Closed the books at a listed company"
    assert detail["decision"]["reviewer"] == TA_EMAIL


def test_the_hm_reads_the_note_as_of_the_share_not_a_later_one(client):
    stem, ids = _mk_screened_role()
    client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                json={"candidate_id": ids[0], "action": "forward", "reason": "first note"})
    client.post(f"/api/roles/{stem}/share-hm", headers=_as("TASpecialist"))
    # the recruiter changes their mind after sharing — the HM still sees the share
    import time

    time.sleep(1.1)  # decision timestamps are to the second
    client.post(f"/api/roles/{stem}/decisions", headers=_as("TASpecialist"),
                json={"candidate_id": ids[0], "action": "not", "reason": "second note"})
    hm = client.get(f"/api/roles/{stem}/review", headers=_as("HiringManager")).json()
    card = next(c for c in hm["cards"] if c["candidate_id"] == ids[0])
    assert (card["decided"], card["decided_reason"]) == ("forward", "first note")
