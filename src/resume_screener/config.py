"""Configuration + secrets (P1).

How configuration flows
-----------------------
Three layers, strictly separated:
1. `.env` (Settings)      — WHERE things are: endpoints, deployment names, Key
                            Vault URI and SECRET NAMES. Zero secret values.
2. `config.yaml` (AppConfig) — HOW the tool behaves: folders, bands, caps,
                            default jurisdiction, cache dir. HR-tunable.
3. Azure Key Vault        — the actual API keys, fetched AT RUNTIME via
                            DefaultAzureCredential and held in memory only.

The Cloud-Admin mandate (CLAUDE.md §0): keys never in .env, code, logs or Git,
never printed. The ApiKeys wrapper below makes accidental printing yield
"<values hidden>" instead of key material.

Error style: everything user-facing raises ConfigError with a plain, actionable
message (what to run / who to ask). The doctor (`screener check`) shows these
verbatim, so keep them one line and secret-free.
"""

from __future__ import annotations

import os
import time
import functools
import logging
import threading
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from resume_screener.pathkey import canon

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """A configuration/auth problem with a message that is safe to show the user."""


def export_env_file(path: str | Path = ".env", *, override: bool = False) -> list[str]:
    """Copy `.env` keys into `os.environ`, and return the names actually set.

    WHY THIS IS NEEDED (found 2026-08-20). `Settings` reads `.env` through
    pydantic-settings, which populates the model and NOTHING ELSE — it never touches
    `os.environ`. But the behavioural switches are not Settings fields: `rbac.py`,
    `modes.py` and `feature_flags.py` all read `os.environ` directly. So every one of
    `DEV_USER`, `DEV_ROLE`, `APP_MODE`, `REQUIRE_SSO`, `LOCAL_SSO`, `DEV_SWITCH`,
    `FEATURE_*` was silently INERT when set in `.env` — the file documented as the place
    to set them. They only ever worked if you also exported them in your shell, which is
    why "I set DEV_ROLE and nothing changed" was a real and baffling experience.

    REAL ENVIRONMENT VARIABLES WIN (`override=False`). Hosted, configuration arrives as
    App Service app settings and a stray committed `.env` must never shadow them; the
    same rule keeps `APP_MODE=uat screener ui` working as a one-off override, and keeps
    the test suite's pinned values (conftest sets `DATABASE_URL=sqlite://`) authoritative.

    Called from the CLI callback only — i.e. for `screener ui|run|check|mcp` — so
    importing the app module in a test never has a file reach into the environment.
    In the container the CMD is uvicorn directly and every value is already a real env
    var, which is the documented contract for `DATA_ROOT` deployments.
    """
    import os

    p = Path(path)
    if not p.exists():
        return []
    applied: list[str] = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # unreadable .env must never stop the app booting
        log.warning("could not read %s: %s", p, exc)
        return []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        # Strip one matched pair of surrounding quotes, the common .env convention.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not override and os.environ.get(key) is not None:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


# --- .env (non-secret values only; CLAUDE.md §8) -----------------------------


class Settings(BaseSettings):
    """Values from .env / process env. The *_secret fields hold Key Vault secret
    NAMES (what to ask the vault for), never the secrets themselves."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    azure_key_vault_uri: str
    azure_openai_api_key_secret: str = "azure-openai-api-key"
    documentintelligence_api_key_secret: str = "document-intelligence-api-key"
    azure_openai_endpoint: str
    azure_openai_chat_deployment: str  # deployment names come from .env, never hard-coded
    azure_openai_embedding_deployment: str
    documentintelligence_endpoint: str
    azure_tenant_id: str | None = None  # optional; makes the az-login hint copy-pasteable
    azure_subscription_id: str | None = None
    # How the app authenticates to Azure OpenAI and Document Intelligence (2026-10-14).
    #   "key" (default) — the two API keys from Key Vault, exactly as before.
    #   "identity"      — an Entra token from DefaultAzureCredential (Managed Identity when
    #                     hosted, `az login` locally); the AI-key secrets are never read.
    # Flip to "identity" only once the identity holds "Cognitive Services OpenAI User" on
    # the Azure OpenAI resource and "Cognitive Services User" on Document Intelligence —
    # role assignments are Cloud Admin's (they need User Access Administrator). After the
    # flip, key access can be switched off on both resources (`--disable-local-auth`).
    ai_auth: Literal["key", "identity"] = "key"

    @field_validator("ai_auth", mode="before")
    @classmethod
    def _normalise_ai_auth(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Name the missing variables (names are safe; values never appear here).
        missing = ", ".join(str(e["loc"][0]).upper() for e in exc.errors())
        raise ConfigError(
            f"Missing/invalid .env values: {missing}. "
            "Copy .env.example to .env and fill in the TODOs."
        ) from exc


# --- database connection (isolated from Azure Settings) -----------------------

# Local dev default: the Docker Postgres from docker-compose.yml (localhost creds
# only — safe to commit). Prod overrides DATABASE_URL to point at Azure Database
# for PostgreSQL; when the URL has no password and targets an Azure host, the
# engine attaches an Entra token at connect time (passwordless — no DB secret in
# .env/code, matching the Key Vault mandate).
DEFAULT_DATABASE_URL = "postgresql+psycopg://screener:screener@localhost:5432/screener"


class DbSettings(BaseSettings):
    """Database settings, kept SEPARATE from `Settings` on purpose: the DB layer
    (migrations, tests, repositories) must work without the full Azure .env being
    present. Only `DATABASE_URL` is read; everything else here has a safe default."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = DEFAULT_DATABASE_URL
    # Which store backs the human-decision (feedback) log. "jsonl" (default) keeps
    # the file-based log so the app runs with zero DB infra; "db" routes decisions
    # to Postgres via the repository. Flipped per environment during the migration
    # off flat files; the Decision contract is identical either way.
    feedback_backend: str = "jsonl"
    # Which store backs the governance trail — the audit log + review sign-off.
    # "jsonl" (default) keeps signals/audit_log.jsonl + review_status.json; "db"
    # routes both through the governance repository (region-scoped). The floor
    # protection, version bump and the "pending HR + Legal review" semantics are
    # identical either way; only the storage moves.
    governance_backend: str = "jsonl"
    # Which store backs the RUN INDEX (the list of screening runs + their meta).
    # "file" (default) scans output/*/run_meta.json; "db" reads/writes a Run row per
    # `screener run` (region + jurisdiction + versions stamped) and lists from the
    # table. Per-candidate JSON + reports + run_meta.json stay on disk either way —
    # only the INDEX moves; read_run_meta still prefers the file and falls back to
    # the row.
    run_index_backend: str = "file"
    # Which store backs SCREENING JOB state (the progress the Screen page polls).
    # "memory" (default) keeps the in-process dict — zero infra, but a restart loses
    # in-flight job state; "db" persists each job to the screening_job table so state
    # survives a restart and is readable by any process (worker-ready, §12). The
    # subprocess model and the {status, done, total} the UI sees are identical either
    # way; only where the state lives changes.
    jobs_backend: str = "memory"
    # Which store backs the SIGNAL REGISTRY (config-as-data, Phase 6a). "file"
    # (default) reads/edits signals/registry.yaml via the comment-preserving ruamel
    # round-trip; "db" reads the signal/excluded_signal rows and edits/publishes them
    # (versioned via registry_version). The governance rails — locked base exclusions,
    # required job-relatedness on new rules, cap>0, version bump + audit entry — are
    # IDENTICAL either way; only the store moves. apply_policy (which turns the
    # registry into the enabled set) is unchanged: it operates on the Pydantic model
    # both backends return.
    registry_backend: str = "file"
    # Which store backs the JURISDICTION POLICY PACKS (config-as-data, Phase 6b).
    # "file" (default) reads/edits policies/*.yaml via ruamel; "db" reads the
    # policy_pack + policy_signal_override rows (versioned — one row set per published
    # edit). resolve_policy's merge (base ⊕ overlay), the US-default fallback and the
    # FLOOR PROTECTION are identical either way; only the store moves.
    policy_backend: str = "file"
    # Which store backs the VERSIONED RUBRICS (config-as-data, Phase 6c). "file"
    # (default) reads/writes jds/<stem>.rubric.vN.yaml; "db" stores rubric +
    # requirement rows (hard gates = kind='hard_gate' rows, P33), keyed by
    # `req_code` (the JD stem the API routes
    # on — a soft link like run.req_code, since the requisition table isn't populated
    # until Phase 6d). Weights renormalize on load and every save is the next version
    # either way; only the store moves.
    rubric_backend: str = "file"
    # Which store backs JD / REQUISITION IDENTITY (config-as-data, Phase 6d). "file"
    # (default) globs jds/ + reads the .req.json sidecars; "db" reads jd_document
    # + requisition rows. Only the ATS's fetch_requisitions moves — résumé FILES and
    # the per-role batches stay on disk (large files stay on disk until P26), so
    # fetch_candidates/fetch_documents are unchanged. Importing requisitions also
    # backfills the requisition_id FK on the rubric/run/screening_job rows that until
    # now carried only the soft req_code link.
    requisitions_backend: str = "file"
    # --- v2 domains (P49) — each defaults to its FILE store, so the zero-DB-infra
    # behavior is unchanged. Flipping one to its DB value reads/writes that domain
    # from Postgres via the matching repository; the contract callers see is identical.
    # HR preferences (signals/preferences.jsonl → hr_preference). "jsonl" | "db".
    prefs_backend: str = "jsonl"
    # The app-event spine (signals/events.jsonl → app_event, append-only). "jsonl" | "db".
    events_backend: str = "jsonl"
    # Hiring-manager takes (output/feedback/hm_takes.jsonl → hm_take). "jsonl" | "db".
    hm_takes_backend: str = "jsonl"
    # Feature flags (signals/features.yaml → feature + region_feature). "yaml" | "db".
    # control:env flags (e.g. hm_portal) stay env-resolved either way.
    flags_backend: str = "yaml"


# The file/JSONL default for every backend switch — what a domain falls back to
# when no usable database is configured (see load_db_settings).
_FILE_DEFAULT_BACKENDS = {
    "feedback_backend": "jsonl", "governance_backend": "jsonl",
    "run_index_backend": "file", "jobs_backend": "memory",
    "registry_backend": "file", "policy_backend": "file",
    "rubric_backend": "file", "requisitions_backend": "file",
    "prefs_backend": "jsonl", "events_backend": "jsonl",
    "hm_takes_backend": "jsonl", "flags_backend": "yaml",
}


# Which (url, switches) combinations we've already warned about — load_db_settings
# runs many times per request, so the notice is logged once per distinct config.
_WARNED_MEMORY_DB: set[tuple] = set()


def _is_memory_sqlite(url: str) -> bool:
    """True for a bare in-memory SQLite URL ("sqlite://", "sqlite:///:memory:").

    Such a URL holds NO schema: it is created empty per connection/process and
    can never be migrated (`screener db upgrade` has nothing to persist to), so
    it is the zero-DB dev/test setting — conftest pins it precisely so the
    offline suite degrades like CI.
    """
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
    except Exception:  # noqa: BLE001 — an unparseable URL is not a usable memory DB
        return False
    return parsed.get_backend_name() == "sqlite" and parsed.database in (None, "", ":memory:")


# Every `_use_db()` check in the codebase builds the settings object, and a single
# dashboard load makes ~400 of those checks. pydantic-settings probes `.env` on
# each construction — a filesystem round trip per check, ~400 per request on the
# share. The object is a pure function of the environment plus the `.env` file,
# so it is rebuilt only when either changes: the env is compared by value on every
# call (tests monkeypatch it), the file at most once a second.
_DB_SETTINGS_CACHE: dict[str, object] = {}
_DB_SETTINGS_RECHECK_SECONDS = 1.0


def _db_env_snapshot() -> tuple:
    return tuple(sorted((k, v) for k, v in os.environ.items()
                        if k == "DATABASE_URL" or k.endswith("_BACKEND")))


def _env_file_state() -> tuple:
    try:
        st = os.stat(".env")
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return (0, 0)


def _db_settings_cached() -> "DbSettings":
    now = time.monotonic()
    env = _db_env_snapshot()
    hit = _DB_SETTINGS_CACHE
    if hit and hit["env"] == env:
        if now - hit["checked"] < _DB_SETTINGS_RECHECK_SECONDS:
            return hit["settings"]  # type: ignore[return-value]
        if _env_file_state() == hit["file"]:
            hit["checked"] = now
            return hit["settings"]  # type: ignore[return-value]
    settings = DbSettings()
    _DB_SETTINGS_CACHE.update(env=env, file=_env_file_state(), checked=now, settings=settings)
    return settings


def load_db_settings() -> DbSettings:
    """The DB settings, with every backend switch COERCED to its file/JSONL
    default when DATABASE_URL is an in-memory SQLite.

    Why: `*_BACKEND=db` against an in-memory SQLite is a contradiction — the
    tables can't exist, so each DB-backed read died with an opaque
    "no such table: requisition" 500 that took the whole dashboard down (only
    the non-DB endpoints kept working). The database is a seam UNDER the engine
    whose documented default is zero-DB-infra behavior, so the honest resolution
    is to serve the file stores and say so, never to 500. A real database
    (Postgres, or a SQLite FILE) is untouched by this.
    """
    settings = _db_settings_cached()
    if not _is_memory_sqlite(settings.database_url):
        return settings
    overridden = [name for name, default in _FILE_DEFAULT_BACKENDS.items()
                  if getattr(settings, name, default) != default]
    if overridden:
        signature = (settings.database_url, tuple(sorted(overridden)))
        if signature not in _WARNED_MEMORY_DB:
            _WARNED_MEMORY_DB.add(signature)
            log.warning(
                "DATABASE_URL is an in-memory SQLite (%s), which holds no schema — "
                "ignoring %s and reading the file/JSONL stores instead. Point "
                "DATABASE_URL at a real database (and run `screener db upgrade`) to "
                "use the DB backends.",
                settings.database_url,
                ", ".join(sorted(n.replace("_backend", "_BACKEND").upper() for n in overridden)),
            )
        settings = settings.model_copy(update={n: _FILE_DEFAULT_BACKENDS[n] for n in overridden})
    return settings


# --- config.yaml (behavior knobs, CLAUDE.md §8) --------------------------------


class AtsConfig(BaseModel):
    provider: str = "local"  # the ATS-port switch: "local" now, iCIMS/… later (§12)
    jd_dir: str = "jds"
    roles_dir: str = "data/roles"  # per-role candidate batches: data/roles/<slug>/resumes/ (P18)
    resumes_dir: str = "data/resumes"  # LEGACY shared folder — fallback + UI-offered migration only


class PolicyConfig(BaseModel):
    default_jurisdiction: str = "IN"  # pilot default; runs can override via --jurisdiction
    policies_dir: str = "policies"


# P51: the scoring knobs moved out of config.yaml into the governed, versioned
# scoring model (signals/scoring_model.vN.yaml — see scoring_model.py). config.py
# deliberately knows nothing about them anymore.


class RedactionConfig(BaseModel):
    enabled: bool = True  # guardrail default ON; `run --no-redact` is an explicit opt-out


class ModelsConfig(BaseModel):
    document_model: str = "prebuilt-layout"  # DI v4 layout model (PDF + DOCX native)


class RuntimeConfig(BaseModel):
    concurrency: int = 4
    cache_dir: str = ".cache"


ExtractionMode = Literal["hybrid", "di-only"]
EXTRACTION_MODES: tuple[str, ...] = ("hybrid", "di-only")


class ExtractionConfig(BaseModel):
    """Where résumé text comes from (InfoSec question 6, 2026-10-14).

    "hybrid" reads text-bearing PDFs and Word files on this machine, so they are
    redacted before any Azure AI service sees them, and sends only files with no
    readable text (scans, photos) to Document Intelligence. "di-only" is the
    behaviour before 2026-10-14: every file goes to Document Intelligence whole."""

    mode: ExtractionMode = "hybrid"
    # Letters + digits a PDF page needs to count as text. A typical résumé page has
    # 1,500–3,000; below this, a page that carries a picture counts as a scan.
    local_min_chars_per_page: int = 200


class AppConfig(BaseModel):
    ats: AtsConfig = AtsConfig()
    policy: PolicyConfig = PolicyConfig()
    redaction: RedactionConfig = RedactionConfig()
    models: ModelsConfig = ModelsConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    extraction: ExtractionConfig = ExtractionConfig()
    signals_file: str = "signals/registry.yaml"


def effective_extraction(cfg: AppConfig) -> tuple[ExtractionConfig, str, str | None]:
    """(the extraction settings in force, where the mode came from, a problem or None).

    EXTRACTION_MODE in the environment wins over config.yaml. It is the switch that
    needs no file edit — and the only one that reaches a hosted share, whose
    config.yaml is seeded once and never overwritten, so a block added to the repo's
    copy never arrives there (the code default does). An invalid value is reported,
    never guessed at: the configured mode stays in force."""
    source = "config.yaml" if "extraction" in cfg.model_fields_set else "default"
    raw = (os.environ.get("EXTRACTION_MODE") or "").strip().lower()
    if not raw:
        return cfg.extraction, source, None
    if raw in EXTRACTION_MODES:
        return cfg.extraction.model_copy(update={"mode": raw}), "EXTRACTION_MODE", None
    return cfg.extraction, source, (
        f"EXTRACTION_MODE={raw!r} is not one of {', '.join(EXTRACTION_MODES)} — ignored; "
        f"{cfg.extraction.mode} stays in force")


# Parsed-config cache keyed on the file's resolved path + (mtime_ns, size).
# service.config() calls this on essentially every request; the file rarely
# changes. NOT functools.lru_cache: tests copy a fresh config.yaml into a
# tmp_path and chdir per test, so the resolved-path key keeps them isolated.
# Returns a deep copy so a caller mutating runtime.concurrency etc. can't poison
# the shared instance.
_CONFIG_CACHE: dict[str, tuple[int, int, AppConfig]] = {}


def load_app_config(path: str | Path = "config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{p} not found — run from the project root.")
    key = canon(p)
    try:
        st = p.stat()
        hit = _CONFIG_CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2].model_copy(deep=True)
    except OSError:
        st = None
    cfg = AppConfig.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    if st is not None:
        _CONFIG_CACHE[key] = (st.st_mtime_ns, st.st_size, cfg)
    return cfg.model_copy(deep=True)


# --- Key Vault secrets (in-memory only) ---------------------------------------


class ApiKeys:
    """The two service API keys, held in memory only.

    Deliberately NOT a pydantic model / dataclass: repr, str and iteration must
    never reveal values (model_dump()/asdict() would). Access is via properties
    only; printing an ApiKeys shows "<values hidden>"."""

    def __init__(self, azure_openai: str, document_intelligence: str) -> None:
        self._aoai = azure_openai
        self._di = document_intelligence

    @property
    def azure_openai(self) -> str:
        return self._aoai

    @property
    def document_intelligence(self) -> str:
        return self._di

    def __repr__(self) -> str:  # never leak values via logging/printing
        return "ApiKeys(<values hidden>)"

    __str__ = __repr__


@functools.lru_cache(maxsize=1)
def _credential():
    """One DefaultAzureCredential per process. Dev: your `az login` token.
    Prod (§12): Managed Identity — same code path, that's the point."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


# The token audience for both AI services when AI_AUTH=identity.
AI_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


def _az_login_hint(settings: Settings) -> str:
    tenant = f" --tenant {settings.azure_tenant_id}" if settings.azure_tenant_id else ""
    return f"Not signed in to Azure. Run:  az login{tenant}"


def fetch_api_keys(settings: Settings) -> ApiKeys:
    """Fetch both API keys from Key Vault. Raises ConfigError with a friendly hint.

    Exception mapping (why each branch exists):
    - ClientAuthenticationError -> not signed in            -> print the az login command
    - ResourceNotFoundError (404) -> secret name missing    -> admin hasn't created it /
      name mismatch in .env. (404 also PROVES access works — RBAC denies with 403.)
    - HttpResponseError 403 -> signed in but no vault role  -> ask for "Key Vault
      Secrets User" on ReesumeScreenerKV (the vault is RBAC, not access-policy).
    - ServiceRequestError -> DNS/network/VPN/firewall       -> check connectivity.
    """
    from azure.core.exceptions import (
        AzureError,
        ClientAuthenticationError,
        HttpResponseError,
        ResourceNotFoundError,
        ServiceRequestError,
    )
    from azure.keyvault.secrets import SecretClient

    try:
        client = SecretClient(
            vault_url=settings.azure_key_vault_uri, credential=_credential()
        )
    except ValueError as exc:  # SecretClient rejects malformed/blank URLs
        raise ConfigError(
            f"AZURE_KEY_VAULT_URI looks invalid ('{settings.azure_key_vault_uri}') — "
            "check .env."
        ) from exc

    def get(name: str) -> str:
        try:
            value = client.get_secret(name).value
        except ClientAuthenticationError as exc:
            raise ConfigError(_az_login_hint(settings)) from exc
        except ResourceNotFoundError as exc:
            raise ConfigError(
                f"Secret '{name}' not found in the vault — check the secret name in .env."
            ) from exc
        except HttpResponseError as exc:
            if exc.status_code == 403:
                raise ConfigError(
                    'Access denied (403): ask your admin for the "Key Vault Secrets '
                    'User" role on ReesumeScreenerKV (the vault uses Azure RBAC).'
                ) from exc
            raise ConfigError(
                f"Key Vault error fetching '{name}' (HTTP {exc.status_code})."
            ) from exc
        except ServiceRequestError as exc:
            raise ConfigError(
                f"Could not reach Key Vault at {settings.azure_key_vault_uri} — "
                "check the URI, your network/VPN, and the vault firewall."
            ) from exc
        except AzureError as exc:  # last-resort catch: still no raw traceback for the user
            raise ConfigError(
                f"Key Vault error fetching '{name}': {type(exc).__name__}."
            ) from exc
        if not value:
            raise ConfigError(f"Secret '{name}' is empty in the vault.")
        return value

    return ApiKeys(
        azure_openai=get(settings.azure_openai_api_key_secret),
        document_intelligence=get(settings.documentintelligence_api_key_secret),
    )


# --- the AI keys as the running app sees them (2026-10-14) ----------------------
#
# fetch_api_keys reads Key Vault on every call. That was fine for a CLI run and wrong
# for a long-lived web process in two opposite ways: the OpenAI client captured the
# FIRST key it was built with and kept it until a restart, so rotating the key in Key
# Vault silently broke the hosted app's JD structuring, résumé splitting and policy
# derivation (each swallows the 401 and falls back to a heuristic); and a naive
# "always fetch" would make two Key Vault calls per model request.
#
# So: one cache, keyed on WHAT is asked for (vault + secret names), re-read after
# AI_KEY_TTL_SECONDS (default 10 minutes), dropped at once by invalidate_api_keys()
# when Azure rejects a key. The OpenAI client reads its key through this on every
# request (azure_clients passes a callable), so a rotation reaches the running app
# within the TTL, or immediately on the first rejected call.

_KEY_CACHE: dict[tuple, tuple[float, ApiKeys]] = {}
_KEY_CACHE_LOCK = threading.Lock()
_DEFAULT_KEY_TTL_SECONDS = 600.0


def _key_ttl_seconds() -> float:
    raw = (os.environ.get("AI_KEY_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_KEY_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        log.warning("AI_KEY_TTL_SECONDS=%r is not a number — using %ss", raw,
                    int(_DEFAULT_KEY_TTL_SECONDS))
        return _DEFAULT_KEY_TTL_SECONDS


def api_keys_for(settings: Settings) -> ApiKeys | None:
    """The AI keys to use right now — or None under AI_AUTH=identity, where no key
    exists and none is read.

    Key mode returns the cached pair while it is younger than AI_KEY_TTL_SECONDS, and
    reads Key Vault otherwise. Raises ConfigError exactly as fetch_api_keys does."""
    if settings.ai_auth == "identity":
        return None
    cache_key = (settings.azure_key_vault_uri, settings.azure_openai_api_key_secret,
                 settings.documentintelligence_api_key_secret)
    now = time.monotonic()
    ttl = _key_ttl_seconds()
    with _KEY_CACHE_LOCK:
        hit = _KEY_CACHE.get(cache_key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    keys = fetch_api_keys(settings)
    with _KEY_CACHE_LOCK:
        _KEY_CACHE[cache_key] = (now, keys)
    return keys


def invalidate_api_keys() -> None:
    """Forget every cached key so the next use reads Key Vault again (a rotated key,
    the doctor's fresh read, tests)."""
    with _KEY_CACHE_LOCK:
        _KEY_CACHE.clear()
