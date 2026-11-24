"""Candidate comments (2026-07-19): the collaboration thread on one candidate.

Comments replace the old "edit the decision note" flow. A decision's reason
stays what it always was — the note captured WITH that decision, attributed to
its recorder. Everything conversational lives here instead: the recruiter and
the assigned hiring manager can each leave any number of comments on the same
candidate, and every comment carries its OWN author, so attribution can never
drift to whoever happened to record the decision (the "Note from hrhead" bug).

`output/feedback/comments.jsonl` — one JSON line per comment, append-only under
file_lock, corrupt lines skipped on read (the decisions/hm_takes pattern).

Line shape: {comment_id, req_id, candidate_id, author, author_name?,
author_role?, text, ts}. Candidate ids are content hashes — no candidate PII
beyond what the author typed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.locking import file_lock

COMMENTS_LOG = Path("output/feedback/comments.jsonl")
REACTIONS_LOG = Path("output/feedback/reactions.jsonl")

# The fixed emoji palette for comment reactions (2026-07-20) — a small, curated
# set (the myscale/Slack pattern), not free emoji entry.
ALLOWED_REACTIONS = ["👍", "❤️", "🎉", "👀", "🙌", "😄"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_comment(
    req_id: str, candidate_id: str, *, author: str, text: str,
    author_name: str = "", author_role: str = "",
    mentions: list[str] | None = None,
    log_path: str | Path = COMMENTS_LOG,
) -> dict:
    """Append one comment and return it. Empty text raises ValueError.
    `mentions` = the emails @-mentioned in the body (the picker supplies them),
    stored so they can be notified and the names highlighted."""
    body = str(text or "").strip()
    if not body:
        raise ValueError("A comment needs some text.")
    record = {
        "comment_id": uuid.uuid4().hex[:12],
        "req_id": req_id,
        "candidate_id": candidate_id,
        "author": author,
        "author_name": author_name,
        "author_role": author_role,
        "text": body,
        "mentions": [str(m).strip() for m in (mentions or []) if str(m).strip()],
        "ts": _now(),
    }
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_comments(
    req_id: str, candidate_id: str | None = None,
    log_path: str | Path = COMMENTS_LOG,
) -> list[dict]:
    """Comments for a requisition (optionally one candidate), oldest first."""
    path = Path(log_path)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(rec, dict) and rec.get("comment_id")):
            continue
        if rec.get("req_id") != req_id:
            continue
        if candidate_id is not None and rec.get("candidate_id") != candidate_id:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("ts", ""))
    return out


def comments_by_candidate(
    req_id: str, log_path: str | Path = COMMENTS_LOG,
) -> dict[str, list[dict]]:
    """candidate_id -> ts-ordered comments — one read for a whole review payload."""
    by_cand: dict[str, list[dict]] = {}
    for rec in list_comments(req_id, log_path=log_path):
        by_cand.setdefault(rec["candidate_id"], []).append(rec)
    return by_cand


# --- reactions (2026-07-20): a toggle log parallel to the immutable comments -------
#
# Comments are append-only and never mutated, so reactions live in their own
# append-only log — one line per toggle, latest per (comment_id, user, emoji)
# wins. `on: false` un-reacts. Corrupt lines are skipped, never fatal.


def toggle_reaction(
    comment_id: str, *, user: str, emoji: str, log_path: str | Path = REACTIONS_LOG,
) -> dict[str, list[str]]:
    """Toggle `user`'s `emoji` reaction on a comment; returns the comment's fresh
    {emoji: [user emails]} map. An unknown emoji raises ValueError."""
    if emoji not in ALLOWED_REACTIONS:
        raise ValueError("That reaction isn't available.")
    current = reactions_for(comment_id, log_path=log_path)
    on = user not in current.get(emoji, [])
    record = {"comment_id": comment_id, "user": user, "emoji": emoji,
              "on": on, "ts": _now()}
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return reactions_for(comment_id, log_path=log_path)


def _reaction_lines(log_path: str | Path) -> list[dict]:
    path = Path(log_path)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("comment_id") and rec.get("emoji"):
            out.append(rec)
    return out


def _aggregate(state: dict[tuple[str, str], bool]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for (user, emoji), on in state.items():
        if on:
            out.setdefault(emoji, []).append(user)
    return out


def reactions_for(
    comment_id: str, log_path: str | Path = REACTIONS_LOG,
) -> dict[str, list[str]]:
    """{emoji: [user emails]} for one comment — latest toggle per (user, emoji) wins."""
    state: dict[tuple[str, str], bool] = {}
    for rec in _reaction_lines(log_path):
        if rec.get("comment_id") == comment_id:
            state[(rec["user"], rec["emoji"])] = bool(rec.get("on"))
    return _aggregate(state)


def reactions_by_comment(
    comment_ids: list[str], log_path: str | Path = REACTIONS_LOG,
) -> dict[str, dict[str, list[str]]]:
    """All reactions for a set of comment ids in one pass — the whole thread."""
    ids = set(comment_ids)
    state: dict[str, dict[tuple[str, str], bool]] = {}
    for rec in _reaction_lines(log_path):
        cid = rec.get("comment_id")
        if cid in ids:
            state.setdefault(cid, {})[(rec["user"], rec["emoji"])] = bool(rec.get("on"))
    return {cid: _aggregate(s) for cid, s in state.items()}
