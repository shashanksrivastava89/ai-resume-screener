"""Data management: the storage map + the two deletion actions (P47).

Resurrected from the Streamlit-era Data page (git ea91a10) as engine functions so
the React Data page and its API routes can drive them, and so they're unit-tested
on synthetic tmp_path trees (never real data/output/.cache — guardrail §6.12).

What deletes what
-----------------
`delete_cache()` empties `.cache/` and recreates it — nothing is lost, the next
screening just recomputes. `delete_candidate_data()` removes every résumé and
screening result from disk — the legacy shared batch, the per-role batches, and
everything under `output/` — but KEEPS `output/feedback/` (the anonymous decision
log, which is the audit trail) and any dot-file placeholders. It also sweeps stray
`.lock` sidecars (from locking.py) left in the candidate trees.

Nothing here touches config-as-data (rules, packs, checklists) or the event spine.
Paths are resolved against the cwd, which `dataroot.bootstrap()` has already
chdir-ed into the DATA_ROOT share when hosted — so `storage_map()` reports the real
absolute locations on that share.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from resume_screener.config import AppConfig
from resume_screener.dataroot import data_root


def dir_size(path: str | Path) -> int:
    """Recursive size in bytes; 0 for a missing path."""
    p = Path(path)
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} GB"


def _clear_dir(path: str | Path, *, keep: set[str] = frozenset()) -> None:
    """Remove every child of `path` except dot-files (placeholders like .gitkeep)
    and any name in `keep`. A no-op when the directory doesn't exist."""
    p = Path(path)
    if not p.exists():
        return
    for child in p.iterdir():
        if child.name.startswith(".") or child.name in keep:
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()


def delete_cache(cache_dir: str | Path) -> None:
    """Empty the cache. Nothing is lost — the next screening recomputes it."""
    path = Path(cache_dir)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _sweep_candidate_sidecars(batch_folder: Path) -> None:
    """Dot-file sidecars that DESCRIBE candidates must die with them: the split
    manifest carries candidate names, and the contact index carries the emails/
    phones de-dup reads. _clear_dir deliberately keeps dot-files (placeholders
    like .gitkeep), so these are removed by name."""
    from resume_screener import batch_prep, resume_split

    batch_prep.clear_prep_files(batch_folder)  # staging + state + contact index + log
    (batch_folder / resume_split.MANIFEST_NAME).unlink(missing_ok=True)


def delete_candidate_data(
    resumes_dir: str | Path,
    roles_dir: str | Path,
    output_dir: str | Path = "output",
) -> None:
    """Remove every résumé and screening result — the legacy shared batch, the
    per-role batches under `roles_dir/<slug>/resumes/`, and everything under
    `output/` — keeping only `output/feedback/` (the anonymous decision log, which
    is the audit trail) and dot-file placeholders. Also sweeps stray `.lock`
    sidecars the locking layer may have left behind."""
    _clear_dir(resumes_dir)

    roles = Path(roles_dir)
    if roles.exists():
        for role in roles.iterdir():
            if role.is_dir():
                _clear_dir(role / "resumes")
                _sweep_candidate_sidecars(role / "resumes")

    _clear_dir(output_dir, keep={"feedback"})

    # Stray lock sidecars (e.g. output/feedback/decisions.jsonl.lock) are harmless
    # to delete when no writer holds them; the lock layer recreates them on demand.
    for base in (Path(resumes_dir), roles, Path(output_dir)):
        if base.exists():
            for lock in base.rglob("*.lock"):
                lock.unlink(missing_ok=True)


# --- storage map (Addendum 04 §A1) --------------------------------------------------


def storage_map(cfg: AppConfig) -> list[dict]:
    """The §A1 storage-map rows with paths relative to the data root + recursive
    sizes — one row per place this tool keeps data. Secrets live only in Key Vault
    (no path, no size)."""
    rows: list[tuple[str, str | None, str, str]] = [
        ("Secrets (API keys)", None, "—", "Fetched from Key Vault at runtime; never on disk"),
        ("Raw résumés (legacy batch)", cfg.ats.resumes_dir, "PDF/DOCX",
         "The legacy shared folder — fallback + migration source only"),
        ("Candidate batches", cfg.ats.roles_dir, "PDF/DOCX",
         "Per-requisition résumé batches"),
        ("Screening results & reports", "output", "JSON/xlsx/csv/html",
         "Shortlists, per-candidate detail, frozen artifacts, audit records"),
        ("Screening checklists", cfg.ats.jd_dir, "Markdown + YAML",
         "JDs + every approved checklist version"),
        ("Judgment rules, proposals & events", "signals", "YAML/JSONL",
         "The rules table, proposals, and the app-event spine"),
        ("Hiring-rule packs", cfg.policy.policies_dir, "YAML",
         "Per-location rules and obligations"),
        ("Human decisions", "output/feedback", "JSONL",
         "The anonymous decision log (append-only) — kept as the audit trail"),
        ("Cache", cfg.runtime.cache_dir, "JSON by hash",
         "Speed only — safe to delete anytime"),
    ]
    out: list[dict] = []
    for kind, rel, fmt, note in rows:
        out.append({
            "kind": kind,
            # Relative to the data root, which the summary states once (2026-10-16,
            # pen-test RS-12): a server's own directory layout is not page content.
            "path": Path(rel).as_posix() if rel else "Azure Key Vault",
            "format": fmt,
            "note": note,
            "size": human_size(dir_size(rel)) if rel else "—",
        })
    return out


def storage_root() -> str:
    """The absolute DATA_ROOT the paths above are resolved under (the cwd locally)."""
    root = data_root()
    return str(Path.cwd() if str(root) == "." else root.resolve())
