"""DATA_ROOT resolution + first-run bootstrap (P26, Addendum 08 Part A).

Why this exists
---------------
On Azure Container Apps the container filesystem is wiped on every restart; the
mutable state (jds/, data/, output/, .cache/, signals/, policies/) must live on
the mounted Azure Files share instead. `DATA_ROOT` points at that mount (`/data`
in the shipped containerapp.patch.yaml).

How it works
------------
Every path in this codebase is a RELATIVE path resolved against the working
directory (that is the flat-file design). So instead of threading a root through
every module, `bootstrap()` — called once at each process entry point (the CLI
callback and the API module import) —:

1. resolves DATA_ROOT (default "." = unchanged local behaviour, a no-op),
2. creates the expected folder skeleton,
3. seeds the repo's committed defaults (config.yaml, rbac.yaml, the signal
   registry, the policy packs) into an EMPTY root — never overwriting a file
   that already exists (the share's copies are the live, HR-edited truth),
4. `os.chdir(DATA_ROOT)` so every relative path lands on the share.

Things that must keep working from the INSTALLED code, not the data root, are
already pinned via `__file__`: alembic.ini (cli._alembic_config) and the built
frontend (api.app._WEB_DIST).

Note for local overrides: with DATA_ROOT set, `.env` is no longer read from the
repo root (pydantic-settings resolves it against the cwd). That is the container
reality — configuration arrives as real environment variables — so set env vars
(or copy `.env` into the data root) when using DATA_ROOT on a laptop.

`.env` into the process environment
-----------------------------------
pydantic-settings reads `.env` for the Azure `Settings` fields, but only into its
own model — it never populates `os.environ`. Modules that read the process
environment directly (rbac's `DEV_ROLE`/`DEV_USER`, `FEATURE_HM_PORTAL`) would
therefore never see a `.env` value, even though `.env.example` and CLAUDE.md §8
tell you to set them there. `load_env_file()` (called from `bootstrap()`, so it
runs at every entry point) closes that gap: a single `.env` is the one place
local dev sets identity/flags. Real environment variables always win — a shell
`DEV_ROLE=…` overrides the file — matching pydantic-settings' own precedence.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

# The repo/install root (…/src/resume_screener/dataroot.py -> parents[2]).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Committed defaults seeded into an empty DATA_ROOT (never overwritten).
_SEED_FILES = (
    "config.yaml",
    "rbac.yaml",
    "signals/features.yaml",
    "signals/scoring_model.v1.yaml",  # seed only; later versions are share-local
    # NON-SCORING reference geography for the location lens. Seeded so a hosted
    # deployment can add a missing city by editing the share, not by cutting a release.
    "signals/places.yaml",
)
_SEED_GLOBS = ("policies/*.yaml",)

# The mutable-folder skeleton (Addendum 08 Part A's share layout).
_DIRS = ("jds", "data/roles", "output", ".cache", "signals", "policies")


def data_root() -> Path:
    """The configured data root; "." (local dev, unchanged behaviour) when unset."""
    return Path(os.environ.get("DATA_ROOT", "").strip() or ".")


def _seed_pairs() -> list[tuple[Path, Path]]:
    """(source-in-repo, relative-target) pairs for the committed defaults."""
    pairs = [(REPO_ROOT / rel, Path(rel)) for rel in _SEED_FILES]
    for pattern in _SEED_GLOBS:
        base = pattern.split("/", 1)[0]
        pairs += [(p, Path(base) / p.name) for p in sorted(REPO_ROOT.glob(pattern))]
    return pairs


def load_env_file(path: str | Path = ".env") -> None:
    """Load simple ``KEY=VALUE`` lines from `.env` into ``os.environ`` WITHOUT
    overriding variables already set in the real environment.

    This is deliberately tiny and dependency-free (no python-dotenv): it only has
    to carry the plain flags that modules read straight from ``os.environ``
    (``DEV_ROLE``, ``DEV_USER``, ``FEATURE_HM_PORTAL``). pydantic-settings still
    parses `.env` itself for the Azure `Settings` fields — this does not replace
    that; it fills the os.environ gap the model layer leaves. Skips blank lines,
    ``#`` comments and malformed lines; strips one layer of matching quotes and a
    leading ``export``. A missing/unreadable `.env` is a silent no-op."""
    p = Path(path)
    if not p.exists():
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or key in os.environ:  # real env vars win (dotenv precedence)
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value


def bootstrap() -> Path:
    """Resolve DATA_ROOT, seed it on first run, and chdir into it. Idempotent and
    cheap; a no-op when DATA_ROOT is unset/"." (the local default). Also loads
    `.env` from the (post-chdir) cwd into os.environ so DEV_ROLE/DEV_USER/flags
    take effect locally."""
    root = data_root()
    if str(root) == ".":
        load_env_file()
        return Path.cwd()
    root = root.resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        for rel in _DIRS:
            (root / rel).mkdir(parents=True, exist_ok=True)
        for source, rel in _seed_pairs():
            target = root / rel
            if source.exists() and not target.exists():  # seed once, NEVER overwrite
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    except PermissionError:
        # Say which user, in the first minute, rather than letting the first write
        # fail somewhere deep in a request (the container runs unprivileged since
        # 2026-10-14 — see serve.py).
        uid = os.getuid() if hasattr(os, "getuid") else "?"
        logging.getLogger(__name__).error(
            "DATA_ROOT %s is not writable by this process (uid %s).", root, uid)
        raise
    os.chdir(root)
    load_env_file()  # from the data root's cwd, matching pydantic-settings
    return root
