"""SQLAlchemy engine construction.

Lazy by design: importing this module never opens a connection, so `screener
--help`, unit tests and Alembic offline mode stay cheap and DB-free until
something actually queries.

Prod is passwordless: when DATABASE_URL targets an Azure Database for PostgreSQL
host and carries no password, we attach an Entra (AAD) access token as the
password at connect time via DefaultAzureCredential — dev uses your `az login`
token, prod uses Managed Identity, the same code path the Key Vault design uses.
No database secret ever lives in .env, code, or Git.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import StaticPool

from resume_screener.config import load_db_settings

# Azure Database for PostgreSQL Entra-auth token scope.
_AZURE_PG_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
_AZURE_PG_HOST_SUFFIX = ".postgres.database.azure.com"

# Cache engines per resolved URL so we get one pool per target, but keep a handle
# to dispose them (tests swap DATABASE_URL and call reset_engine()).
_engines: dict[str, Engine] = {}


def _is_azure_passwordless(url_str: str) -> bool:
    url = make_url(url_str)
    host = url.host or ""
    return host.endswith(_AZURE_PG_HOST_SUFFIX) and not url.password


def _attach_entra_token(engine: Engine) -> None:
    @event.listens_for(engine, "do_connect")
    def _provide_token(dialect, conn_rec, cargs, cparams):  # noqa: ANN001, ANN202
        from resume_screener.config import _credential

        cparams["password"] = _credential().get_token(_AZURE_PG_SCOPE).token


def get_engine(url: str | None = None) -> Engine:
    """The (cached) engine for `url` or DATABASE_URL. Built on first use."""
    resolved = url or load_db_settings().database_url
    engine = _engines.get(resolved)
    if engine is None:
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        # Fail FAST on an unreachable / half-open Postgres — e.g. the default
        # localhost:5432 with no live server, or a stale Docker port-forward to a
        # stopped container that accepts the TCP connection but never completes
        # the handshake. Without a bound, that connect BLOCKS FOREVER, and the
        # one default DB-toucher (service.region_context, which degrades to
        # "unrestricted" ON ERROR) hangs instead of degrading — taking /api/me and
        # the whole app down. A short connect_timeout turns the hang into a quick
        # error the caller already handles. sqlite/other dialects ignore this.
        parsed = make_url(resolved)
        backend = parsed.get_backend_name()
        if backend == "postgresql":
            kwargs["connect_args"] = {"connect_timeout": 3}
        elif backend == "sqlite":
            # SQLite from a THREADED server (zero-DB dev sets DATABASE_URL=sqlite://).
            # Two defaults bite here, and both surface as
            # "SQLite objects created in a thread can only be used in that same
            # thread" — noisy pool errors that escalate into request-path 500s on a
            # long-lived worker, because FastAPI runs every sync route on a
            # ROTATING threadpool thread:
            #   1. sqlite3 refuses cross-thread connection reuse -> check_same_thread.
            #   2. a MEMORY db ("sqlite://", "sqlite:///:memory:") is per-connection,
            #      so the default thread-local pool hands each thread its own EMPTY
            #      database. StaticPool shares the one connection instead, so the
            #      schema (and the degrade-gracefully reads) stay consistent.
            kwargs["connect_args"] = {"check_same_thread": False}
            if parsed.database in (None, "", ":memory:"):
                kwargs["poolclass"] = StaticPool
        engine = create_engine(resolved, **kwargs)
        if _is_azure_passwordless(resolved):
            _attach_entra_token(engine)
        _engines[resolved] = engine
    return engine


def reset_engine() -> None:
    """Dispose and forget all cached engines (test hygiene / DATABASE_URL swaps)."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
