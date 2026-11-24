"""Hash-keyed JSON disk cache (P2) — why re-runs are near-free (CLAUDE.md §5).

The caching pattern used everywhere in this system
---------------------------------------------------
key = content_hash(<every input that affects the output>). No TTLs and no manual
invalidation: when any input changes (file bytes, redacted text, rubric content,
model/deployment name, prompt version constant), the key changes and the old
entry is simply never read again. Corrupt entries read as a miss and get
rewritten. That's the whole model — if you add a cached step, put EVERYTHING
that shapes its output into the key, including a PROMPT_VERSION constant you
bump when you edit the prompt.

Namespaces map one-to-one to pipeline stages: ingest/ profile/ findings/ embed/…
so a stage can be nuked selectively (delete the folder) during debugging.

Note: .cache/ holds extracted/redacted resume text, so it is gitignored and
listed in the end-of-PoC deletion checklist (setup doc §G).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def content_hash(*parts: str | bytes) -> str:
    """Deterministic sha256 over any mix of strings/bytes — the standard cache key."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8") if isinstance(part, str) else part)
        h.update(b"\x1f")  # separator so ("ab","c") != ("a","bc")
    return h.hexdigest()


class JsonCache:
    """Namespaced JSON entries at <cache_dir>/<namespace>/<key>.json."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.root = Path(cache_dir)

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None  # corrupt/partial entry counts as a miss and gets rewritten

    def set(self, namespace: str, key: str, value: Any) -> None:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write can't leave a half-written entry
        # that would later json-fail (it would read as a miss anyway, but this
        # keeps the cache clean under concurrency too).
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
