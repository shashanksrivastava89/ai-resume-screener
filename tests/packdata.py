"""Governed values the tests must FOLLOW, not pin (2026-09-06).

The policy packs, the feature-flag catalog and the scoring model are governed DATA:
a policy maker publishes a new version through the app (second-leader approved),
and the committed files under policies/ and signals/ are that published state —
commit 9aa5093 snapshotted the pilot's publishes (IN pack v2 moved the PII purge
duty from 365 to 400 days; the iCIMS reopen/clone flags were switched on; the
scoring model's batch-quality window widened). Fourteen tests had the ORIGINAL seed
literals baked in and turned red on a change that was legitimate by construction.

A test of the MACHINERY (merge, plumbing, date arithmetic) reads the expected value
from the raw pack file here and asserts the code reproduces it — a merge that
ignored the overlay would still fail, because the raw YAML and the merged result
would disagree. A test that needs a specific VALUE (a flag that must be off, a
threshold the arithmetic depends on) sets it explicitly in its own fixture instead
of assuming the seed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def pack(code: str) -> dict:
    """The raw (unmerged) policy pack as committed — the value a publish wrote."""
    return yaml.safe_load((REPO_ROOT / "policies" / f"{code}.yaml").read_text(encoding="utf-8"))


def pack_days(code: str) -> tuple[int | None, int | None]:
    """(keep_at_least_days, purge_by_days) straight from the pack file."""
    data = pack(code)
    return data.get("keep_at_least_days"), data.get("purge_by_days")


def plus_days(iso_ts: str, days: int) -> str:
    """closed_at + N days as an ISO date — the same arithmetic retention.py and
    service.retention_for perform, written independently so the test still checks it."""
    return (datetime.fromisoformat(iso_ts) + timedelta(days=days)).date().isoformat()


def set_flag_defaults(features_path: Path, defaults: dict[str, bool]) -> None:
    """Force the given flags' `default` in a COPIED features.yaml (never the seed).
    Tests that need a flag off say so here rather than trusting the catalog's
    current toggle, which the SuperAdmin surface changes at will."""
    doc = yaml.safe_load(features_path.read_text(encoding="utf-8"))
    for key, value in defaults.items():
        doc["features"][key]["default"] = value
    features_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
