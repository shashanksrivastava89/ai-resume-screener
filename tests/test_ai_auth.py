"""AI_AUTH (2026-10-14): how the app authenticates to Azure OpenAI + Document Intelligence.

Key mode (the default) reads the API keys from Key Vault through a TTL cache, hands the
OpenAI client its key as a callable so a rotation reaches a running process, and
recovers ONCE from a rejected key. Identity mode never reads an AI key at all. Offline —
every Azure surface here is a fake.
"""

import time
from types import SimpleNamespace

import httpx
import openai
import pytest
from azure.core.credentials import AccessToken

from resume_screener import azure_clients, config
from resume_screener.config import ApiKeys, ConfigError, Settings


def _settings(**over) -> Settings:
    base = dict(
        azure_key_vault_uri="https://kv.example.vault.azure.net/",
        azure_openai_endpoint="https://aoai.example.openai.azure.com/",
        azure_openai_chat_deployment="chat",
        azure_openai_embedding_deployment="emb",
        documentintelligence_endpoint="https://di.example.cognitiveservices.azure.com/",
    )
    base.update(over)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _no_ambient_auth(monkeypatch):
    for name in ("AI_AUTH", "AI_KEY_TTL_SECONDS"):
        monkeypatch.delenv(name, raising=False)


class FakeCredential:
    def __init__(self):
        self.scopes = []

    def get_token(self, *scopes, **kwargs):
        self.scopes.append(scopes)
        return AccessToken("entra-token", int(time.time()) + 3600)


class Vault:
    """Stands in for fetch_api_keys: counts reads and returns the CURRENT key pair."""

    def __init__(self, aoai="k1", di="d1"):
        self.aoai, self.di, self.reads = aoai, di, 0

    def __call__(self, settings):
        self.reads += 1
        return ApiKeys(self.aoai, self.di)


def _rejected(status=401):
    request = httpx.Request("POST", "https://aoai.example/openai/v1/chat/completions")
    cls = openai.AuthenticationError if status == 401 else openai.PermissionDeniedError
    return cls("denied", response=httpx.Response(status, request=request), body=None)


class RecordingOpenAI:
    """Replaces openai.OpenAI: records the constructor arguments, answers the probe."""

    built: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        RecordingOpenAI.built.append(self)

        def create(**_):
            message = SimpleNamespace(content="OK")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def test_key_is_the_default_and_the_value_is_normalised(monkeypatch):
    assert _settings().ai_auth == "key"
    monkeypatch.setenv("AI_AUTH", " Identity ")
    assert _settings().ai_auth == "identity"
    monkeypatch.setenv("AI_AUTH", "password")
    with pytest.raises(Exception):  # an unknown mode is a configuration error, not a guess
        _settings()


def test_identity_mode_never_reads_an_ai_key(monkeypatch):
    def vault(_settings):
        raise AssertionError("identity mode must not read an AI key")

    cred = FakeCredential()
    monkeypatch.setattr(config, "fetch_api_keys", vault)
    monkeypatch.setattr(config, "_credential", lambda: cred)
    monkeypatch.setattr(openai, "OpenAI", RecordingOpenAI)
    s = _settings(ai_auth="identity")

    assert config.api_keys_for(s) is None
    info = azure_clients.get_openai_client(s)
    assert info.auth == "identity"
    api_key = RecordingOpenAI.built[-1].kwargs["api_key"]
    assert callable(api_key) and api_key() == "entra-token"
    assert cred.scopes[-1] == (config.AI_TOKEN_SCOPE,)

    di = azure_clients.get_document_intelligence_client(s)
    assert di._config.credential is cred


def test_key_mode_reads_the_vault_once_per_ttl(monkeypatch):
    vault = Vault()
    monkeypatch.setattr(config, "fetch_api_keys", vault)
    s = _settings()

    assert config.api_keys_for(s).azure_openai == "k1"
    config.api_keys_for(s)
    assert vault.reads == 1  # a cache hit, not a second Key Vault round trip

    config.invalidate_api_keys()
    config.api_keys_for(s)
    assert vault.reads == 2

    monkeypatch.setenv("AI_KEY_TTL_SECONDS", "0")
    config.api_keys_for(s)
    config.api_keys_for(s)
    assert vault.reads == 4  # TTL 0 = read every time


def test_the_key_client_follows_a_rotated_key(monkeypatch):
    vault = Vault()
    monkeypatch.setattr(config, "fetch_api_keys", vault)
    monkeypatch.setattr(openai, "OpenAI", RecordingOpenAI)
    s = _settings()

    info = azure_clients.get_openai_client(s, config.api_keys_for(s))
    assert info.auth == "key"
    api_key = RecordingOpenAI.built[-1].kwargs["api_key"]
    assert callable(api_key), "the client must not capture the key it was built with"
    assert api_key() == "k1"

    vault.aoai = "k2"  # rotated in Key Vault
    config.invalidate_api_keys()  # what with_auth_recovery does on a rejected call
    assert api_key() == "k2"


def test_a_rejected_key_is_re_read_once_and_the_call_succeeds(monkeypatch):
    vault = Vault()
    monkeypatch.setattr(config, "fetch_api_keys", vault)
    s = _settings()
    config.api_keys_for(s)  # the process has a cached key
    seen = []

    def call():
        seen.append(config.api_keys_for(s).azure_openai)
        if len(seen) == 1:
            raise _rejected(401)
        return "ok"

    vault.aoai = "k2"
    assert azure_clients.with_auth_recovery(call, auth="key") == "ok"
    assert seen == ["k1", "k2"] and vault.reads == 2


def test_a_key_rejected_twice_is_a_plain_config_error(monkeypatch):
    monkeypatch.setattr(config, "fetch_api_keys", Vault())

    def call():
        raise _rejected(401)

    with pytest.raises(ConfigError, match="rejected the API key"):
        azure_clients.with_auth_recovery(call, auth="key")


def test_an_identity_rejection_names_the_missing_role_and_does_not_retry():
    attempts = []

    def call():
        attempts.append(1)
        raise _rejected(403)

    with pytest.raises(ConfigError, match="Cognitive Services OpenAI User"):
        azure_clients.with_auth_recovery(call, auth="identity")
    assert attempts == [1]  # nothing to refresh, so nothing to retry


def test_parse_structured_recovers_from_a_rotated_key(monkeypatch):
    monkeypatch.setattr(config, "fetch_api_keys", Vault())
    temperatures = []

    def parse(**kwargs):
        temperatures.append(kwargs["temperature"])
        if len(temperatures) == 1:
            raise _rejected(401)
        message = SimpleNamespace(refusal=None, parsed="PARSED")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    assert azure_clients.parse_structured(client, "chat", [], object) == "PARSED"
    assert temperatures == [0, 0]  # still pinned to 0 on the retry


def test_embeddings_recover_from_a_rotated_key(monkeypatch):
    from resume_screener import embed

    monkeypatch.setattr(config, "fetch_api_keys", Vault())
    calls = []

    def create(**kwargs):
        calls.append(kwargs["input"])
        if len(calls) == 1:
            raise _rejected(401)
        return SimpleNamespace(data=[], usage=None)

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    embed._create_embeddings(client, "emb", ["text"])
    assert calls == [["text"], ["text"]]


def test_describe_auth_names_the_mode():
    assert "AI_AUTH=key" in azure_clients.describe_auth(_settings())
    assert "no AI key is read" in azure_clients.describe_auth(_settings(ai_auth="identity"))
