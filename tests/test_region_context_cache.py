"""Region access is resolved from the database a few times a minute, not per request.

Three queries — the region list, the user row, their grants — ran on EVERY request
for data that no API, CLI or admin page can change: `identity.grant_role` has no
caller outside `db seed` and the importers, so a grant moves only through an
out-of-band admin action (2026-09-10).

A short window is the deliberate trade. The alternative, a version-stamp query per
request, would cost most of what it saves — and unlike the decision log, where an
append happens through the app itself and must be seen at once, nothing in the
running app can change a grant.
"""
from __future__ import annotations

import pytest

from resume_screener.api import service


@pytest.fixture(autouse=True)
def clean():
    service.invalidate_region_context()
    yield
    service.invalidate_region_context()


def _count_queries(monkeypatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(service, "_region_context_query",
                        lambda user: (seen.append(user), {"all": True, "codes": [],
                                                          "ungranted": False})[1])
    return seen


def test_a_second_request_does_not_ask_the_database_again(monkeypatch):
    seen = _count_queries(monkeypatch)
    for _ in range(5):
        service.region_context.__wrapped__("ta@example.com")
    assert seen == ["ta@example.com"], f"asked the database {len(seen)} times"


def test_each_person_is_cached_separately(monkeypatch):
    seen = _count_queries(monkeypatch)
    service.region_context.__wrapped__("ta@example.com")
    service.region_context.__wrapped__("hr@example.com")
    service.region_context.__wrapped__("ta@example.com")
    assert seen == ["ta@example.com", "hr@example.com"]


def test_the_window_expires(monkeypatch):
    seen = _count_queries(monkeypatch)
    service.region_context.__wrapped__("ta@example.com")
    monkeypatch.setattr(service.time, "monotonic",
                        lambda: 10_000_000.0)   # far past any expiry
    service.region_context.__wrapped__("ta@example.com")
    assert len(seen) == 2, "a stale entry was served past its window"


def test_the_caller_cannot_mutate_what_the_next_caller_sees(monkeypatch):
    """The dict is handed out by value: a caller that edits it must not poison
    everyone else's access answer."""
    _count_queries(monkeypatch)
    first = service.region_context.__wrapped__("ta@example.com")
    first["all"] = False
    first["codes"].append("MADE-UP")
    second = service.region_context.__wrapped__("ta@example.com")
    assert second["all"] is True and "MADE-UP" not in second["codes"]


def test_an_administrator_can_force_a_fresh_read(monkeypatch):
    seen = _count_queries(monkeypatch)
    service.region_context.__wrapped__("ta@example.com")
    service.invalidate_region_context()
    service.region_context.__wrapped__("ta@example.com")
    assert len(seen) == 2
