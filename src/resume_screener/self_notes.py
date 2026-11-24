"""Private self-notes (2026-07-20): a recruiter's OWN scratch note on a candidate.

Distinct from the two other note surfaces:
  - the shared COMMENT thread (comments.py) — visible to the whole recruiting
    side and the assigned HM;
  - the DECISION note (Decision.reason) — captured WITH a decision, run-scoped.

A self-note is PRIVATE to its author, saveable on its own without recording a
decision (the "no way to save them" pilot bug), and — because it is keyed by
candidate_id (a content hash, never a run) — it survives a re-screen for as long
as the candidate exists.

output/feedback/self_notes.jsonl — one JSON line per save, append-only under
file_lock, latest line per (req_id, candidate_id, author) wins on read. Saving
empty text CLEARS the note (read returns None).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock

SELF_NOTES_LOG = Path("output/feedback/self_notes.jsonl")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_note(
    req_id: str, candidate_id: str, *, author: str, text: str,
    log_path: str | Path = SELF_NOTES_LOG,
) -> dict:
    """Append the author's note for one candidate; empty text clears it. Returns
    the record written."""
    record = {"req_id": req_id, "candidate_id": candidate_id, "author": author,
              "text": str(text or "").strip(), "ts": _now()}
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def get_note(
    req_id: str, candidate_id: str, author: str,
    log_path: str | Path = SELF_NOTES_LOG,
) -> dict | None:
    """The author's latest note for one candidate as {text, ts}, or None when
    never saved or cleared to empty. Corrupt lines are skipped, never fatal."""
    path = Path(log_path)
    if not path.exists() or not str(author or "").strip():
        return None
    me = str(author).strip().lower()
    latest: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (rec.get("req_id") == req_id and rec.get("candidate_id") == candidate_id
                and str(rec.get("author", "")).strip().lower() == me):
            latest = rec
    if latest is None or not str(latest.get("text", "")).strip():
        return None
    return {"text": latest["text"], "ts": latest.get("ts")}
