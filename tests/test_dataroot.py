"""P26: DATA_ROOT resolution + first-run bootstrap (container persistence seam)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from resume_screener import dataroot


@pytest.fixture()
def restore_cwd():
    old = Path.cwd()
    yield
    os.chdir(old)


def test_default_is_a_noop(monkeypatch, restore_cwd):
    monkeypatch.delenv("DATA_ROOT", raising=False)
    before = Path.cwd()
    assert dataroot.bootstrap() == before
    assert Path.cwd() == before


def test_bootstrap_seeds_empty_root_and_chdirs(monkeypatch, tmp_path, restore_cwd):
    root = tmp_path / "share"
    monkeypatch.setenv("DATA_ROOT", str(root))
    result = dataroot.bootstrap()

    assert result == root.resolve() == Path.cwd()
    # committed defaults seeded
    for rel in ("config.yaml", "rbac.yaml", "signals/scoring_model.v1.yaml",
                "signals/features.yaml", "policies/IN.yaml", "policies/base.yaml"):
        assert (root / rel).exists(), rel
    # the mutable-folder skeleton (Addendum 08 Part A share layout)
    for rel in ("jds", "data/roles", "output", ".cache"):
        assert (root / rel).is_dir(), rel


def test_bootstrap_never_overwrites_existing_files(monkeypatch, tmp_path, restore_cwd):
    root = tmp_path / "share"
    monkeypatch.setenv("DATA_ROOT", str(root))
    dataroot.bootstrap()

    # HR edited the live registry on the share; a restart must not reset it.
    registry = root / "signals" / "registry.yaml"
    registry.write_text("version: 99\nsignals: []\nexcluded: []\n", encoding="utf-8")
    dataroot.bootstrap()
    assert "version: 99" in registry.read_text(encoding="utf-8")


def test_bootstrap_is_idempotent(monkeypatch, tmp_path, restore_cwd):
    root = tmp_path / "share"
    monkeypatch.setenv("DATA_ROOT", str(root))
    first = dataroot.bootstrap()
    second = dataroot.bootstrap()
    assert first == second == Path.cwd()


def test_load_env_file_populates_os_environ(monkeypatch, tmp_path, restore_cwd):
    # A .env in the cwd feeds plain flags into os.environ (the gap pydantic-settings
    # leaves) — so rbac's DEV_ROLE/DEV_USER and FEATURE_HM_PORTAL take effect locally.
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("FEATURE_HM_PORTAL", raising=False)
    # DEV_USER too, and for the same reason as the other two: load_env_file
    # deliberately lets a REAL env var win over a .env line (see the next test), so a
    # developer whose own repo .env sets DEV_USER — the documented way to pick a local
    # identity — would fail this test with a confusing "you@x.com != <their address>".
    # The test has to own every variable it asserts on.
    monkeypatch.delenv("DEV_USER", raising=False)
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        "DEV_ROLE=TASpecialist,SuperAdmin\n"
        'DEV_USER="you@x.com"\n'
        "export FEATURE_HM_PORTAL=1\n"
        "MALFORMED_LINE_NO_EQUALS\n",
        encoding="utf-8",
    )
    os.chdir(tmp_path)
    dataroot.bootstrap()
    assert os.environ.get("DEV_ROLE") == "TASpecialist,SuperAdmin"
    assert os.environ.get("DEV_USER") == "you@x.com"  # surrounding quotes stripped
    assert os.environ.get("FEATURE_HM_PORTAL") == "1"  # leading `export` stripped


def test_load_env_file_does_not_override_real_env(monkeypatch, tmp_path, restore_cwd):
    # A shell-set variable must win over the .env line (dotenv precedence).
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.setenv("DEV_ROLE", "IT")
    (tmp_path / ".env").write_text("DEV_ROLE=HRHead\n", encoding="utf-8")
    os.chdir(tmp_path)
    dataroot.bootstrap()
    assert os.environ.get("DEV_ROLE") == "IT"
