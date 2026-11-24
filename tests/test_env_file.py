"""`.env` must actually reach the switches it documents (2026-08-20).

pydantic-settings reads `.env` into the `Settings` model and touches nothing else. But
the behavioural switches — APP_MODE, DEV_USER/DEV_ROLE, REQUIRE_SSO, LOCAL_SSO,
DEV_SWITCH, FEATURE_* — are read straight off `os.environ` by rbac/modes/feature_flags.
So setting any of them in `.env` did nothing at all, silently, in the very file that
documents them as the place to set them. `export_env_file()` closes that gap; these
tests pin the precedence rule that makes it safe to do so.
"""

from __future__ import annotations

import os

import pytest

from resume_screener.config import export_env_file


@pytest.fixture(autouse=True)
def _restore_environ():
    """Snapshot and restore the WHOLE environment around every test here.

    monkeypatch cannot help: it only reverts changes monkeypatch itself made, and the
    function under test writes to `os.environ` directly and by design. Without this,
    `APP_MODE=uat` set by the first test leaks into every later test in the process and
    the dev-identity suites start failing far away from the cause — which is exactly what
    happened the first time these tests ran."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _write(tmp_path, body: str):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_values_land_in_os_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("APP_MODE", raising=False)
    env = _write(tmp_path, "DEV_ROLE=TASpecialist\nAPP_MODE=uat\n")
    assert set(export_env_file(env)) == {"DEV_ROLE", "APP_MODE"}
    assert os.environ["DEV_ROLE"] == "TASpecialist"
    assert os.environ["APP_MODE"] == "uat"


def test_a_real_environment_variable_always_wins(tmp_path, monkeypatch):
    """THE safety rule. Hosted, configuration arrives as App Service app settings and a
    committed `.env` inside the image must never shadow them. It is also what keeps
    `APP_MODE=uat screener ui` working as a one-off override of the file."""
    monkeypatch.setenv("APP_MODE", "prod")
    env = _write(tmp_path, "APP_MODE=dev\n")
    assert "APP_MODE" not in export_env_file(env)
    assert os.environ["APP_MODE"] == "prod"


def test_override_is_available_but_not_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "prod")
    env = _write(tmp_path, "APP_MODE=dev\n")
    assert export_env_file(env, override=True) == ["APP_MODE"]
    assert os.environ["APP_MODE"] == "dev"


def test_comments_blanks_exports_and_quotes(tmp_path, monkeypatch):
    for k in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
        monkeypatch.delenv(k, raising=False)
    env = _write(tmp_path, """
# a comment
   # an indented comment

A_KEY=plain
export B_KEY=exported
C_KEY="double quoted"
D_KEY='single quoted'
NOT_A_PAIR
""")
    export_env_file(env)
    assert os.environ["A_KEY"] == "plain"
    assert os.environ["B_KEY"] == "exported"
    assert os.environ["C_KEY"] == "double quoted"
    assert os.environ["D_KEY"] == "single quoted"
    assert "NOT_A_PAIR" not in os.environ


def test_an_empty_value_is_preserved_as_empty(tmp_path, monkeypatch):
    """`DEV_SWITCH=` is what a half-filled .env produces, and modes._env_flag treats
    empty as "unset, let the mode decide". That only works if we set it to "" rather
    than skipping it or inventing a value."""
    monkeypatch.delenv("DEV_SWITCH", raising=False)
    export_env_file(_write(tmp_path, "DEV_SWITCH=\n"))
    assert os.environ["DEV_SWITCH"] == ""


def test_a_missing_file_is_not_an_error(tmp_path):
    assert export_env_file(tmp_path / "nope.env") == []


def test_secrets_policy_is_unaffected(tmp_path, monkeypatch):
    """This loader moves whatever is in `.env` into the environment, so it must not
    become an excuse to put a key there. The mandate is unchanged: `.env` holds secret
    NAMES, Key Vault holds values. Pinned as a reminder, and because the loader is a
    plausible place for someone to later add "…and fetch the secret too"."""
    monkeypatch.delenv("AZURE_OPENAI_API_KEY_SECRET", raising=False)
    export_env_file(_write(tmp_path, "AZURE_OPENAI_API_KEY_SECRET=azure-openai-api-key\n"))
    # A NAME, not a value — the vault is still the only source of key material.
    assert os.environ["AZURE_OPENAI_API_KEY_SECRET"] == "azure-openai-api-key"
