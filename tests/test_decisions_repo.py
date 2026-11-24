"""Phase 2 tests — decisions repository + the feedback backend switch. Offline.

Two layers:
1. the decisions repo directly (append-only, ordering, latest-wins, reason rule);
2. feedback.record_decision/load_decisions with FEEDBACK_BACKEND=db pointed at an
   in-memory SQLite, proving the file->DB switch preserves the JSONL contract.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from tests.dbharness import make_test_engine as _sqlite_engine

from resume_screener import feedback
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import decisions as repo
from resume_screener.models import Decision


def _decision(candidate="cid1", reason="Good treasury depth", action="agree",
              ts="2026-07-06T10:00:00+00:00") -> Decision:
    return Decision(
        candidate_id=candidate, run_id="run-1", suggestion="Strong (94)",
        human_action=action, final_band=None, reason=reason, reviewer="Shashank",
        versions={"rubric": "role-1.rubric.v1.yaml (v1)", "policy_pack": "IN"}, ts=ts,
    )




@pytest.fixture
def session() -> Session:
    eng = _sqlite_engine()
    sess = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield sess
    finally:
        sess.close()
        eng.dispose()


# --- repo directly -----------------------------------------------------------------


def test_add_and_list_roundtrip(session):
    repo.add_decision(session, _decision())
    session.commit()
    loaded = repo.list_decisions(session)
    assert len(loaded) == 1
    assert loaded[0] == _decision()  # full contract round-trips


def test_reason_required_in_repo(session):
    with pytest.raises(ValueError, match="reason"):
        repo.add_decision(session, _decision(reason="   "))
    session.rollback()
    assert repo.list_decisions(session) == []


def test_append_only_latest_wins(session):
    repo.add_decision(session, _decision(reason="first look", action="agree"))
    repo.add_decision(session, _decision(reason="changed my mind", action="override",
                                         ts="2026-07-06T11:00:00+00:00"))
    session.commit()
    assert len(repo.list_decisions(session)) == 2  # history preserved
    latest = repo.latest_decision_for(session, "run-1", "cid1")
    assert latest.human_action == "override" and latest.reason == "changed my mind"


def test_fk_stamps_persist(session):
    repo.add_decision(session, _decision(), requisition_id=None, region_id=None,
                      reviewer_id=None)
    session.commit()
    assert repo.list_decisions(session)[0].candidate_id == "cid1"


# --- feedback backend switch (file -> db) ------------------------------------------


@pytest.fixture
def db_backend(monkeypatch):
    """Point feedback at an in-memory SQLite via FEEDBACK_BACKEND=db."""
    eng = _sqlite_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)
    monkeypatch.setattr(feedback, "_use_db", lambda: True)
    try:
        yield
    finally:
        eng.dispose()


def test_feedback_db_backend_roundtrip(db_backend):
    assert feedback.record_decision(_decision()) is None  # db backend returns None
    loaded = feedback.load_decisions()
    assert len(loaded) == 1 and loaded[0] == _decision()


def test_feedback_db_backend_reason_required(db_backend):
    with pytest.raises(ValueError, match="reason"):
        feedback.record_decision(_decision(reason=""))
    assert feedback.load_decisions() == []


def test_feedback_db_backend_append_only(db_backend):
    feedback.record_decision(_decision(reason="first look", action="agree"))
    feedback.record_decision(_decision(reason="changed my mind", action="override",
                                       ts="2026-07-06T11:00:00+00:00"))
    decisions = feedback.load_decisions()
    assert len(decisions) == 2
    latest = feedback.latest_decision_for(decisions, "run-1", "cid1")
    assert latest.human_action == "override" and latest.reason == "changed my mind"


# --- the requisition on a DB decision + the AI's verdict at rest (2026-10-14) ----------
#
# Under FEEDBACK_BACKEND=db a decision used to lose its requisition (the row had no
# column for it), so a re-screen never carried it forward. And every row kept the AI's
# suggestion and the reviewer's note in plain text; with DECISION_KEY_SECRET set they
# are sealed into one envelope (fieldcrypto) and the plain columns are left empty.

import os  # noqa: E402

from resume_screener import fieldcrypto  # noqa: E402
from resume_screener.db.models.runtime import Decision as DecisionRow  # noqa: E402


@pytest.fixture
def decision_key(monkeypatch):
    key = os.urandom(32)
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    fieldcrypto.set_key_provider(lambda version: ("k1", key))
    yield key
    fieldcrypto.set_key_provider(None)


def _raw_rows(session) -> list[DecisionRow]:
    return list(session.query(DecisionRow).order_by(DecisionRow.id))


def test_req_id_round_trips_and_feeds_carry_forward(db_backend):
    from resume_screener.api import service

    feedback.record_decision(_decision().model_copy(update={"req_id": "finance-analyst"}))
    [loaded] = feedback.load_decisions()
    assert loaded.req_id == "finance-analyst"
    # the per-requisition index is what carries a decision across a re-screen
    _by_run, by_req = service._decision_index()
    assert by_req[("finance-analyst", "cid1")].reason == "Good treasury depth"


def test_without_a_key_rows_are_plain_as_before(session):
    repo.add_decision(session, _decision())
    session.commit()
    [row] = _raw_rows(session)
    assert row.verdict_enc is None
    assert row.suggestion == "Strong (94)" and row.reason == "Good treasury depth"


def test_with_a_key_the_verdict_is_sealed_and_reads_back_whole(session, decision_key):
    repo.add_decision(session, _decision())
    session.commit()
    [row] = _raw_rows(session)
    assert row.suggestion is None and row.reason == ""
    assert row.verdict_enc.startswith("v1:k1:")
    assert "Strong" not in row.verdict_enc and "treasury" not in row.verdict_enc
    # who, what and when stay plain: the audit fact that a human decided
    assert row.reviewer == "Shashank" and row.human_action == "agree"
    assert repo.list_decisions(session) == [_decision()]
    assert repo.latest_decision_for(session, "run-1", "cid1") == _decision()


def test_plain_and_sealed_rows_read_side_by_side(session, monkeypatch):
    repo.add_decision(session, _decision(reason="before the key"))
    session.commit()
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    key = os.urandom(32)
    fieldcrypto.set_key_provider(lambda version: ("k1", key))
    repo.add_decision(session, _decision(reason="after the key", ts="2026-07-06T11:00:00+00:00"))
    session.commit()
    assert [d.reason for d in repo.list_decisions(session)] == ["before the key", "after the key"]
    assert [r.verdict_enc is None for r in _raw_rows(session)] == [True, False]


def test_a_configured_key_that_cannot_be_read_refuses_the_write(session, monkeypatch):
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")

    def unreachable(_version):
        raise fieldcrypto.DecisionKeyUnavailable("vault unreachable")

    fieldcrypto.set_key_provider(unreachable)
    with pytest.raises(fieldcrypto.DecisionKeyUnavailable):
        repo.add_decision(session, _decision())
    session.rollback()
    assert _raw_rows(session) == []  # never a plain-text fallback


def test_an_unreadable_key_shows_a_placeholder_instead_of_failing_the_read(session, decision_key):
    repo.add_decision(session, _decision())
    session.commit()

    def unreachable(_version):
        raise fieldcrypto.DecisionKeyUnavailable("vault unreachable")

    fieldcrypto.set_key_provider(unreachable)
    [d] = repo.list_decisions(session)
    assert d.reason == fieldcrypto.UNREADABLE_NOTE and d.suggestion == ""
    assert d.human_action == "agree" and d.reviewer == "Shashank"


def test_sealing_existing_rows_is_idempotent(session):
    repo.add_decision(session, _decision(candidate="a"))
    repo.add_decision(session, _decision(candidate="b", reason="Strong on GST"))
    session.commit()
    os.environ[fieldcrypto.ENV_SECRET] = "decision-field-key"
    try:
        key = os.urandom(32)
        fieldcrypto.set_key_provider(lambda version: ("k1", key))
        assert repo.encrypt_plain_rows(session) == (2, 0, 0)
        session.commit()
        assert repo.encrypt_plain_rows(session) == (0, 0, 2)
        assert all(r.suggestion is None and r.reason == "" for r in _raw_rows(session))
        assert [d.reason for d in repo.list_decisions(session)] == ["Good treasury depth",
                                                                    "Strong on GST"]
    finally:
        os.environ.pop(fieldcrypto.ENV_SECRET, None)


class _Vault:
    """A fake Key Vault secret with versions; a versionless read returns `current`.
    `disabled` versions refuse to be read, like a disabled secret version."""

    def __init__(self):
        self.versions = {"k1": os.urandom(32)}
        self.current = "k1"
        self.disabled: set[str] = set()

    def __call__(self, version):
        v = version or self.current
        if v in self.disabled:
            raise fieldcrypto.DecisionKeyUnavailable(f"version {v} is disabled")
        return v, self.versions[v]

    def rotate(self, new):
        self.versions[new] = os.urandom(32)
        self.current = new
        fieldcrypto.set_key_provider(self)  # a fresh process, or the 10-minute re-read


def test_a_rotation_rewrap_moves_every_row_to_the_new_key(session, monkeypatch):
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    vault = _Vault()
    fieldcrypto.set_key_provider(vault)
    repo.add_decision(session, _decision(candidate="a"))
    repo.add_decision(session, _decision(candidate="b", reason="Strong on GST"))
    session.commit()

    vault.rotate("k2")
    repo.add_decision(session, _decision(candidate="c", reason="after rotation"))
    session.commit()
    assert [r.verdict_enc.split(":")[1] for r in _raw_rows(session)] == ["k1", "k1", "k2"]

    assert repo.encrypt_plain_rows(session, rewrap=True) == (0, 2, 1)
    session.commit()
    assert {r.verdict_enc.split(":")[1] for r in _raw_rows(session)} == {"k2"}
    assert repo.encrypt_plain_rows(session, rewrap=True) == (0, 0, 3)

    # only now may the old version be disabled — and nothing needs it any more
    vault.disabled.add("k1")
    fieldcrypto.set_key_provider(vault)
    assert [d.reason for d in repo.list_decisions(session)] == [
        "Good treasury depth", "Strong on GST", "after rotation"]


def test_a_rewrap_never_replaces_a_verdict_it_could_not_read(session, monkeypatch):
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    vault = _Vault()
    fieldcrypto.set_key_provider(vault)
    repo.add_decision(session, _decision(candidate="a"))
    session.commit()
    before = [r.verdict_enc for r in _raw_rows(session)]

    vault.rotate("k2")
    vault.disabled.add("k1")  # disabled too early: the old rows cannot be opened
    fieldcrypto.set_key_provider(vault)
    with pytest.raises(fieldcrypto.DecisionKeyUnavailable):
        repo.encrypt_plain_rows(session, rewrap=True)
    session.rollback()
    assert [r.verdict_enc for r in _raw_rows(session)] == before
