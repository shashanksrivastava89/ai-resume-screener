"""The AI's verdict on a recorded decision, encrypted at rest (InfoSec, 2026-10-14).

InfoSec asked that "the AI decision is stored in masked format in the database — only
the app and the HR team can read the actual verdict". Every decision row stored, in
plain text, `suggestion` — the AI's band and score the reviewer was shown, e.g.
"Strong (91)" — and `reason`, the reviewer's own note. Anyone who could read the
database could read both.

With a key configured (DECISION_KEY_SECRET = the NAME of a Key Vault secret), those
two are written as ONE envelope instead, and the plain columns are left empty:

    v1:<key version>:<base64(nonce ‖ ciphertext ‖ tag)>

- AES-256-GCM, a fresh 96-bit nonce per row, and the row's (run_id, candidate_id) as
  associated data — an envelope copied onto another row fails to open rather than
  reading as that row's verdict.
- The key is a 32-byte secret (base64) in Key Vault, read with the app's own identity
  (DefaultAzureCredential — Managed Identity when hosted), held in memory only.
- Rotation is a new secret version. New rows use the current version; every envelope
  names the version that sealed it, so older rows stay readable until
  `screener db encrypt-decisions --rewrap` re-seals them under the new one — only then
  may the old version be disabled.
- The secret carries NO expiry date, deliberately: Key Vault refuses to return an
  expired secret, so an expiry would turn every sealed note into a placeholder on
  that day. The rotation cadence lives in the runbook, not in the secret.
- What stays plain, deliberately: who decided (`reviewer`), what they decided
  (`human_action`, and `final_band`, which is only that decision's bucket) and when.
  That is the audit record a human made each call; it is not the AI's verdict.

No key configured = nothing changes: plain columns, exactly as before (the zero-infra
default every backend switch keeps). A key configured but unreachable never falls
back to plain text: a WRITE is refused with a plain message, a READ shows a
placeholder note instead of failing the page.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from typing import Callable

from resume_screener.config import ConfigError

log = logging.getLogger(__name__)

ENV_SECRET = "DECISION_KEY_SECRET"
PREFIX = "v1"
UNREADABLE_NOTE = "[note unavailable — the decision key could not be read]"
_CURRENT_TTL_SECONDS = 600.0


class DecisionKeyUnavailable(ConfigError):
    """The key is configured but cannot be read — say so; never write plain text."""


# version|None -> (version, 32-byte key). The default reads Key Vault; tests inject.
KeyProvider = Callable[[str | None], tuple[str, bytes]]

_lock = threading.Lock()
_keys: dict[str, bytes] = {}
_current: tuple[float, str] | None = None
_provider: KeyProvider | None = None
_warned_read = False


def secret_name() -> str:
    """The Key Vault secret holding the key, or "" when encryption is off. Read from
    the environment on every call (App Service settings hosted; `.env` reaches it
    through the CLI), so tests and a restarted process always see the current value."""
    return (os.environ.get(ENV_SECRET) or "").strip()


def enabled() -> bool:
    return bool(secret_name())


def set_key_provider(provider: KeyProvider | None) -> None:
    """Swap where keys come from (tests); None restores Key Vault. Clears the cache."""
    global _provider, _current
    with _lock:
        _provider = provider
        _keys.clear()
        _current = None


def _vault_key(version: str | None) -> tuple[str, bytes]:
    from azure.core.exceptions import AzureError
    from azure.keyvault.secrets import SecretClient

    from resume_screener.config import _credential, load_settings

    try:
        uri = load_settings().azure_key_vault_uri
    except ConfigError:
        uri = (os.environ.get("AZURE_KEY_VAULT_URI") or "").strip()
    if not uri:
        raise DecisionKeyUnavailable(
            f"{ENV_SECRET} is set but AZURE_KEY_VAULT_URI is not — the key cannot be read.")
    name = secret_name()
    try:
        secret = SecretClient(vault_url=uri, credential=_credential()).get_secret(name, version)
    except AzureError as exc:
        raise DecisionKeyUnavailable(
            f"Could not read the decision key '{name}' from Key Vault "
            f"({type(exc).__name__}).") from exc
    try:
        key = base64.b64decode(secret.value or "", validate=True)
    except ValueError as exc:
        raise DecisionKeyUnavailable(f"The decision key '{name}' is not valid base64.") from exc
    if len(key) != 32:
        raise DecisionKeyUnavailable(
            f"The decision key '{name}' must be 32 random bytes (base64); it is {len(key)}.")
    return str(secret.properties.version or ""), key


def _fetch(version: str | None) -> tuple[str, bytes]:
    provider = _provider or _vault_key
    return provider(version)


def _current_key() -> tuple[str, bytes]:
    """The version new rows are sealed with — re-read every few minutes so a rotated
    secret reaches a running process without a restart."""
    global _current
    now = time.monotonic()
    with _lock:
        cur = _current
        if cur is not None and now - cur[0] < _CURRENT_TTL_SECONDS and cur[1] in _keys:
            return cur[1], _keys[cur[1]]
    version, key = _fetch(None)
    with _lock:
        _keys[version] = key
        _current = (now, version)
    return version, key


def _key_for(version: str) -> bytes:
    with _lock:
        hit = _keys.get(version)
    if hit is not None:
        return hit
    got_version, key = _fetch(version)
    with _lock:
        _keys[got_version or version] = key
    return key


def _aad(run_id: str, candidate_id: str) -> bytes:
    return f"decision:{run_id}:{candidate_id}".encode("utf-8")


def seal(fields: dict, run_id: str, candidate_id: str) -> str:
    """Encrypt the verdict fields of one decision row into an envelope."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    version, key = _current_key()
    nonce = os.urandom(12)
    body = AESGCM(key).encrypt(nonce, json.dumps(fields, ensure_ascii=False).encode("utf-8"),
                               _aad(run_id, candidate_id))
    return f"{PREFIX}:{version}:{base64.b64encode(nonce + body).decode('ascii')}"


def current_version() -> str:
    """The key version new rows are sealed with (the rewrap sweep compares to it)."""
    return _current_key()[0]


def envelope_version(envelope: str) -> str:
    """The key version an envelope was sealed with."""
    return envelope.split(":", 2)[1]


def open_envelope(envelope: str, run_id: str, candidate_id: str) -> dict:
    """Decrypt an envelope sealed for THIS row. Raises on tampering, a wrong row, or a
    key that cannot be read — callers that render choose how to degrade."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    prefix, version, payload = envelope.split(":", 2)
    if prefix != PREFIX:
        raise ValueError(f"Unknown envelope format '{prefix}'.")
    raw = base64.b64decode(payload, validate=True)
    data = AESGCM(_key_for(version)).decrypt(raw[:12], raw[12:], _aad(run_id, candidate_id))
    return json.loads(data.decode("utf-8"))


def open_or_placeholder(envelope: str, run_id: str, candidate_id: str) -> dict:
    """open_envelope for reads that must not take a page down: on any failure the
    note says it could not be read and the AI suggestion is left empty."""
    global _warned_read
    try:
        return open_envelope(envelope, run_id, candidate_id)
    except Exception as exc:  # noqa: BLE001 — degrade the one row, log once
        if not _warned_read:
            _warned_read = True
            log.warning("A decision verdict could not be decrypted (%s) — showing a "
                        "placeholder note; check %s and the app's Key Vault access.",
                        type(exc).__name__, ENV_SECRET)
        return {"suggestion": "", "reason": UNREADABLE_NOTE}


def status() -> tuple[str, str]:
    """(state, detail) for the doctor: plain | encrypted | unavailable."""
    if not enabled():
        return "plain", f"{ENV_SECRET} unset — decision verdicts are stored as plain text"
    try:
        version, _key = _current_key()
    except Exception as exc:  # noqa: BLE001
        return "unavailable", str(exc)
    return "encrypted", (f"AES-256-GCM with Key Vault secret '{secret_name()}'"
                         + (f", version {version[:8]}…" if version else ""))
