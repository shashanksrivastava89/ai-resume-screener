"""Azure service clients (P1, CLAUDE.md §2).

Two services, one probe, one shared retry policy:

- Document Intelligence — extraction. Plain client, nothing clever.
- Azure OpenAI — chat + embeddings through the OpenAI SDK pointed at the Azure
  "v1" endpoint (base_url = <endpoint>/openai/v1/, NO api-version parameter —
  the v1 GA API routes to the latest GA surface automatically). The `model`
  argument of every call is the DEPLOYMENT name from .env, never a model id.

Why the probe exists: some resources don't expose the v1 path. get_openai_client
sends ONE tiny chat call; on 404 it retries via the classic AzureOpenAI client
pinned to the last GA api-version and reports which path won (`.path`), so the
doctor can display it. The result is cached for the process — the probe never
repeats. If BOTH paths 404, the deployment name itself is wrong, which is the
error we raise.

How the clients authenticate (AI_AUTH, 2026-10-14)
-------------------------------------------------
- `key` (default): the API keys from Key Vault. The v1 client is given its key as a
  CALLABLE that reads config.api_keys_for() on every request, so a key rotated in
  Key Vault reaches the running process within AI_KEY_TTL_SECONDS — or at once, on
  the first rejected call (with_auth_recovery drops the cache and retries once).
  Before this the client kept the first key it was built with until a restart.
- `identity`: an Entra token for Cognitive Services from DefaultAzureCredential
  (Managed Identity hosted). No AI key is read anywhere. A 401/403 then means the
  identity lacks its role, and the error says so instead of retrying.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from resume_screener.config import ApiKeys, ConfigError, Settings

log = logging.getLogger(__name__)

# Last-generation GA data-plane api-version, used ONLY if the v1 endpoint is
# unavailable on this resource (verified against the Azure api-version
# lifecycle docs, July 2026).
FALLBACK_API_VERSION = "2024-10-21"

IDENTITY_ROLE_HINT = (
    "AI_AUTH=identity, but Azure rejected this app's identity — it needs the "
    "'Cognitive Services OpenAI User' role on the Azure OpenAI resource and "
    "'Cognitive Services User' on Document Intelligence. Set AI_AUTH=key until "
    "Cloud Admin has assigned them."
)
REJECTED_KEY_HINT = (
    "Azure OpenAI rejected the API key from Key Vault even after re-reading it — "
    "check that the key secret holds a current key for this resource."
)


def get_document_intelligence_client(settings: Settings, keys: ApiKeys | None = None):
    """A Document Intelligence client for the configured AI_AUTH mode. `keys` is only
    a shortcut for callers that already hold them; key mode reads the cache otherwise."""
    from azure.ai.documentintelligence import DocumentIntelligenceClient

    if settings.ai_auth == "identity":
        from resume_screener.config import _credential

        credential = _credential()  # the SDK's default scope is Cognitive Services
    else:
        from azure.core.credentials import AzureKeyCredential

        from resume_screener.config import api_keys_for

        credential = AzureKeyCredential((keys or api_keys_for(settings)).document_intelligence)
    return DocumentIntelligenceClient(
        endpoint=settings.documentintelligence_endpoint, credential=credential,
    )


@dataclass
class OpenAIClientInfo:
    client: object  # openai.OpenAI (v1 path) or openai.AzureOpenAI (fallback)
    path: str  # human-readable: which API path the probe selected (shown by the doctor)
    probe_reply: str | None = None
    auth: str = "key"  # "key" | "identity" — how this client authenticates


# Probe result for this process; a CLI invocation probes at most once.
_openai_info: OpenAIClientInfo | None = None
_openai_lock = threading.Lock()


def reset() -> None:
    """Forget the probed client — tests, and after a rejected key on the fallback
    client (whose key is a plain string; see get_openai_client)."""
    global _openai_info
    with _openai_lock:
        _openai_info = None


def _key_provider(settings: Settings):
    """The v1 client's key, read through the TTL cache on every request."""
    from resume_screener.config import api_keys_for

    def provider() -> str:
        keys = api_keys_for(settings)
        if keys is None:  # only reachable if AI_AUTH changed under a running process
            raise ConfigError("AI_AUTH changed to identity while the key client was live — restart.")
        return keys.azure_openai

    return provider


def _token_provider():
    from azure.identity import get_bearer_token_provider

    from resume_screener.config import AI_TOKEN_SCOPE, _credential

    return get_bearer_token_provider(_credential(), AI_TOKEN_SCOPE)


def _current_auth() -> str:
    info = _openai_info
    if info is not None:
        return info.auth
    import os

    return "identity" if (os.environ.get("AI_AUTH") or "").strip().lower() == "identity" else "key"


def with_auth_recovery(call, auth: str | None = None):
    """Run `call()`; recover ONCE from a 401/403.

    Key mode: the key was probably rotated in Key Vault after this process cached it.
    Drop the cached keys (the v1 client reads its key per request, so the retry uses
    the fresh one), forget the probed client (so a fallback client, whose key is a
    string, gets rebuilt by the next caller), and try once more. A second rejection
    is a ConfigError in plain words, not a stack trace.
    Identity mode: there is nothing to refresh — the identity lacks its role."""
    import openai

    mode = auth or _current_auth()
    try:
        return call()
    except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
        if mode == "identity":
            raise ConfigError(IDENTITY_ROLE_HINT) from exc
        from resume_screener.config import invalidate_api_keys

        # Logs the HTTP status only, never the key.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        log.warning("Azure OpenAI rejected the cached API key (HTTP %s) — re-reading Key "
                    "Vault and retrying once", getattr(exc, "status_code", "?"))
        invalidate_api_keys()
        reset()
    try:
        return call()
    except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
        raise ConfigError(REJECTED_KEY_HINT) from exc


def describe_auth(settings: Settings) -> str:
    """One line for the doctor and the docs: how this process authenticates."""
    if settings.ai_auth == "identity":
        return ("managed-identity / Entra token for Cognitive Services (AI_AUTH=identity) "
                "— no AI key is read")
    return (f"API keys from Key Vault (AI_AUTH=key) — secrets "
            f"'{settings.azure_openai_api_key_secret}' and "
            f"'{settings.documentintelligence_api_key_secret}', re-read on rotation")


def get_openai_client(settings: Settings, keys: ApiKeys | None = None) -> OpenAIClientInfo:
    """Build the chat/embeddings client, probing the v1 endpoint once per process.

    `keys` is accepted for call-site compatibility; the client never keeps it — key
    mode reads the current key per request (see the module docstring)."""
    global _openai_info
    if _openai_info is not None:
        return _openai_info

    from openai import AzureOpenAI, NotFoundError, OpenAI

    endpoint = settings.azure_openai_endpoint.rstrip("/")
    deployment = settings.azure_openai_chat_deployment
    probe_messages = [{"role": "user", "content": "Reply with exactly: OK"}]
    identity = settings.ai_auth == "identity"
    auth = "identity" if identity else "key"
    provider = _token_provider() if identity else _key_provider(settings)

    v1_client = OpenAI(base_url=f"{endpoint}/openai/v1/", api_key=provider)
    try:
        reply = with_auth_recovery(
            lambda: v1_client.chat.completions.create(model=deployment, messages=probe_messages),
            auth=auth,
        )
        info = OpenAIClientInfo(
            client=v1_client,
            path="v1 endpoint (…/openai/v1/, no api-version)",
            probe_reply=reply.choices[0].message.content,
            auth=auth,
        )
        with _openai_lock:
            _openai_info = info
        return info
    except NotFoundError:
        # 404 is ambiguous here: "no v1 path on this resource" OR "no such
        # deployment". The fallback attempt below disambiguates — if it also
        # 404s, the deployment name is the problem.
        pass

    if identity:
        fallback = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=provider,
            api_version=FALLBACK_API_VERSION,
        )
    else:
        # This SDK's AzureOpenAI never re-reads a callable api_key (its _prepare_options
        # skips the refresh the v1 client does), so the fallback carries the key as a
        # string; with_auth_recovery resets the probe so the next caller rebuilds it.
        fallback = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=provider(),
            api_version=FALLBACK_API_VERSION,
        )
    try:
        reply = with_auth_recovery(
            lambda: fallback.chat.completions.create(model=deployment, messages=probe_messages),
            auth=auth,
        )
    except NotFoundError as exc:
        raise ConfigError(
            f"Deployment '{deployment}' not found on {endpoint} — check "
            "AZURE_OPENAI_CHAT_DEPLOYMENT in .env against your AI Foundry deployments."
        ) from exc
    info = OpenAIClientInfo(
        client=fallback,
        path=f"fallback AzureOpenAI (api-version={FALLBACK_API_VERSION}) — v1 rejected",
        probe_reply=reply.choices[0].message.content,
        auth=auth,
    )
    with _openai_lock:
        _openai_info = info
    return info


class UsageMeter:
    """Process-wide token/call accumulator behind run_meta's cost estimate.

    `screener run` resets it at start and snapshots it at the end. Cached
    results never reach the API, so they add nothing — which is exactly why
    re-runs are near-free and the meter shows it. Thread-safe because the run
    pipeline calls models from a worker pool."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.chat_calls = 0
            self.chat_prompt_tokens = 0
            self.chat_completion_tokens = 0
            self.embedding_calls = 0
            self.embedding_tokens = 0

    def add_chat(self, usage) -> None:
        with self._lock:
            self.chat_calls += 1
            if usage is not None:
                self.chat_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.chat_completion_tokens += getattr(usage, "completion_tokens", 0) or 0

    def add_embeddings(self, usage) -> None:
        with self._lock:
            self.embedding_calls += 1
            if usage is not None:
                self.embedding_tokens += getattr(usage, "total_tokens", 0) or 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "chat_calls": self.chat_calls,
                "chat_prompt_tokens": self.chat_prompt_tokens,
                "chat_completion_tokens": self.chat_completion_tokens,
                "embedding_calls": self.embedding_calls,
                "embedding_tokens": self.embedding_tokens,
            }


METER = UsageMeter()


def _llm_transient(exc: BaseException) -> bool:
    """Errors worth retrying: rate limits, server errors, network blips.
    NOT worth retrying: auth failures, bad requests, content refusals —
    retrying those just burns time and tokens."""
    import openai

    return isinstance(
        exc,
        (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APIConnectionError,
            openai.APITimeoutError,
        ),
    )


# Shared retry policy for ALL model calls (chat parse + embeddings): exponential
# backoff 2s->60s, 5 attempts, then re-raise the real error for the caller.
llm_retry = retry(
    retry=retry_if_exception(_llm_transient),
    wait=wait_exponential(multiplier=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)


@llm_retry
def parse_structured(client, model: str, messages: list[dict], response_model):
    """Every structured LLM call in the system goes through here (CLAUDE.md §2).

    - response_model is a Pydantic class; the SDK converts it to a strict JSON
      schema and validates the reply — callers receive a typed object, never
      raw JSON to parse.
    - temperature=0 is pinned here (determinism guardrail §6.10); do not add a
      temperature parameter to callers.
    - The parse helper moved namespaces across SDK versions, hence the getattr:
      modern SDKs have chat.completions.parse, older ones beta.chat.completions.parse.
    """
    parse = getattr(client.chat.completions, "parse", None)
    if parse is None:
        parse = client.beta.chat.completions.parse
    response = with_auth_recovery(lambda: parse(
        model=model, messages=messages, response_format=response_model, temperature=0
    ))
    METER.add_chat(getattr(response, "usage", None))  # feeds run_meta's cost estimate
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        raise ConfigError(f"The model refused a structured request: {message.refusal}")
    if message.parsed is None:
        raise ConfigError("The model returned no parseable structured output.")
    return message.parsed
