"""The AI's verdict on a decision, encrypted at rest (InfoSec, 2026-10-14).

fieldcrypto seals a decision row's suggestion + note into one AES-256-GCM envelope
bound to the row; the key comes from Key Vault (faked here). Offline.
"""

from __future__ import annotations

import base64
import os

import pytest

from resume_screener import fieldcrypto


class Keys:
    """A fake Key Vault secret with versions; `current` is what a versionless read
    returns. Counts reads so caching is observable."""

    def __init__(self):
        self.versions = {"v1": os.urandom(32)}
        self.current = "v1"
        self.reads = 0

    def __call__(self, version):
        self.reads += 1
        v = version or self.current
        return v, self.versions[v]


@pytest.fixture
def keys(monkeypatch):
    k = Keys()
    monkeypatch.setenv(fieldcrypto.ENV_SECRET, "decision-field-key")
    fieldcrypto.set_key_provider(k)
    yield k
    fieldcrypto.set_key_provider(None)


FIELDS = {"suggestion": "Strong (91)", "reason": "Spoke to them — strong on treasury"}


def test_off_without_a_configured_secret(monkeypatch):
    monkeypatch.delenv(fieldcrypto.ENV_SECRET, raising=False)
    assert not fieldcrypto.enabled()
    assert fieldcrypto.status()[0] == "plain"


def test_round_trip_and_the_envelope_reveals_nothing(keys):
    env = fieldcrypto.seal(FIELDS, "run-1", "cand-1")
    assert env.startswith("v1:v1:")
    assert "Strong" not in env and "treasury" not in env
    assert fieldcrypto.open_envelope(env, "run-1", "cand-1") == FIELDS


def test_two_seals_of_the_same_verdict_differ(keys):
    a = fieldcrypto.seal(FIELDS, "run-1", "cand-1")
    b = fieldcrypto.seal(FIELDS, "run-1", "cand-1")
    assert a != b  # a fresh nonce per row


def test_an_envelope_moved_to_another_row_does_not_open(keys):
    env = fieldcrypto.seal(FIELDS, "run-1", "cand-1")
    with pytest.raises(Exception):
        fieldcrypto.open_envelope(env, "run-1", "cand-2")
    with pytest.raises(Exception):
        fieldcrypto.open_envelope(env, "run-2", "cand-1")


def test_a_tampered_envelope_does_not_open(keys):
    env = fieldcrypto.seal(FIELDS, "run-1", "cand-1")
    prefix, version, payload = env.split(":", 2)
    raw = bytearray(base64.b64decode(payload))
    raw[-1] ^= 0x01
    forged = f"{prefix}:{version}:{base64.b64encode(bytes(raw)).decode()}"
    with pytest.raises(Exception):
        fieldcrypto.open_envelope(forged, "run-1", "cand-1")


def test_rows_sealed_before_a_rotation_stay_readable(keys):
    old = fieldcrypto.seal(FIELDS, "run-1", "cand-1")
    keys.versions["v2"] = os.urandom(32)
    keys.current = "v2"
    fieldcrypto.set_key_provider(keys)   # a fresh process after the rotation
    new = fieldcrypto.seal(FIELDS, "run-1", "cand-2")
    assert new.startswith("v1:v2:")
    assert fieldcrypto.open_envelope(old, "run-1", "cand-1") == FIELDS
    assert fieldcrypto.open_envelope(new, "run-1", "cand-2") == FIELDS


def test_the_key_is_read_once_not_per_row(keys):
    for i in range(5):
        fieldcrypto.seal(FIELDS, "run-1", f"cand-{i}")
    assert keys.reads == 1


def test_an_unreadable_key_degrades_a_read_to_a_placeholder(keys):
    env = fieldcrypto.seal(FIELDS, "run-1", "cand-1")

    def broken(_version):
        raise fieldcrypto.DecisionKeyUnavailable("vault unreachable")

    fieldcrypto.set_key_provider(broken)
    out = fieldcrypto.open_or_placeholder(env, "run-1", "cand-1")
    assert out == {"suggestion": "", "reason": fieldcrypto.UNREADABLE_NOTE}
    state, detail = fieldcrypto.status()
    assert state == "unavailable" and "unreachable" in detail
