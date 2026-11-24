"""Shared test-database engine factory.

Default (CI/dev): a fresh in-memory SQLite per call — fast, offline, no server.
Opt-in: set TEST_DATABASE_URL to run the SAME repository suite against a real
Postgres (a production-fidelity pass — batch migrations as direct ALTERs, BigInteger
identity, stricter type coercion). For Postgres the schema is dropped + recreated on
each call (= per test) for isolation; SQLite gets a brand-new in-memory DB anyway.

Every repo test builds its engine via `make_test_engine()` (aliased locally as
`_sqlite_engine` for historical continuity), so one env var flips the whole suite.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

import resume_screener.db.models  # noqa: F401 — registers every table on Base.metadata
from resume_screener.db.base import Base


def make_test_engine() -> Engine:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if url:
        engine = create_engine(url, future=True)
        # Clean slate per test. Sequential pytest → isolation; the suite owns this DB.
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        return engine

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # noqa: ANN001, ANN202 — SQLite needs FKs turned on
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine
