"""User-scoped HR screening preferences (P39): an event-sourced store.

`signals/preferences.jsonl` holds one JSON line per transition; the LATEST line
per `pref_id` wins (same fold as decisions/events). A preference is a plain-
language nudge the owner wants applied to every requisition they manage — bounded
to +2 points and applied by the P40 scoring stage (this module changes NO scores).

The verdict is the fairness check's: prefcheck's governed phrase lists, escalated by
the prefcheck_semantic intent screen (`screen_pref`, what the API passes as `check`).

Lifecycle (hold-and-review since 2026-10-16, pen-test RS-01):
  - ok      -> status "pending"  (HR Head sign-off; the checks found no conflicts)
  - warn    -> status "pending"  (HR Head sign-off, with the fairness check's reason)
  - blocked -> status "blocked"  (no approval path — never applies)
Only an APPROVED preference applies (`applies`). Until 2026-10-16 an `ok` verdict
went live with nobody looking ("active"), and the pen test got 10 of 14 disguised
protected-attribute preferences live that way: a list of phrases cannot be complete,
so a person now signs every one off. A line stored as "active" before then reads as
"pending" — no migration, and it stops applying until someone approves it. Nobody
approves their own preference (the owner ≠ approver rule change requests follow).
Approve stamps approved_by/at + review_due (+6 months); an approval that is past
its review_due reads as display status "rereview" (suspended — contributes 0 in
P40 until re-approved). Withdraw/decline are terminal until re-added.

Every transition also emits a `pref_*` event on the app spine (events.jsonl) — the
governance audit log stays reserved for config publishes.
"""

from __future__ import annotations

import calendar
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from resume_screener.events import emit_event
from resume_screener.locking import file_lock
from resume_screener.prefcheck import TextCheck, classify_text

PREFERENCES_LOG = Path("signals/preferences.jsonl")
# Seeded defaults (P40); the LIVE values come from the governed scoring model
# (signals/scoring_model.vN.yaml) via _pref_cap()/_review_months() below.
CAP_POINTS = 2.0            # the bounded nudge P40 applies per active/approved pref
REVIEW_MONTHS = 6           # approvals expire and need periodic re-review


def _pref_cap() -> float:
    from resume_screener.scoring_model import load_scoring_model

    return load_scoring_model().preferences.per_preference_cap_points


def _review_months() -> int:
    from resume_screener.scoring_model import load_scoring_model

    return load_scoring_model().preferences.review_months

# status stored on each line
STATUS_ACTIVE = "active"  # legacy (before 2026-10-16): never written now, read as pending
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DECLINED = "declined"
STATUS_BLOCKED = "blocked"
STATUS_WITHDRAWN = "withdrawn"
# derived (never stored): an approved pref past its review_due
STATUS_REREVIEW = "rereview"

_LEVEL_TO_STATUS = {"ok": STATUS_PENDING, "warn": STATUS_PENDING, "blocked": STATUS_BLOCKED}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_months(iso: str, months: int) -> str:
    dt = datetime.fromisoformat(iso)
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day).isoformat(timespec="seconds")


def pack_exclusion_keywords(policies_dir: str | Path = "policies") -> list[str]:
    """The UNION of every shipped pack's additional_exclusions, as keyword phrases
    (the exclusion id with underscores as spaces). User-scoped prefs are checked
    against the strictest set anywhere, so a factor barred in ANY jurisdiction is
    flagged. Empty today (no pack ships additional_exclusions) — a forward hook."""
    from resume_screener.policy import PolicyError, load_pack

    keywords: set[str] = set()
    for path in sorted(Path(policies_dir).glob("*.yaml")):
        try:
            pack = load_pack(path)
        except PolicyError:
            continue
        for exc in pack.additional_exclusions:
            keywords.add(exc.id.replace("_", " "))
    return sorted(keywords)


def classify_pref(text: str, policies_dir: str | Path = "policies") -> TextCheck:
    """Classify a preference against the fairness floor + the pack-exclusion union.

    A preference is user-global (not tied to any req/jurisdiction), so it is
    checked against the STRICTEST UNION of every jurisdiction's blocked patterns
    — a factor blocked in ANY region is surfaced here. Per-req scoring later
    re-checks against that req's own jurisdiction (pref_match), so a rule that is
    only blocked in, say, California still applies to India reqs."""
    from resume_screener.policy import blocked_patterns_union

    return classify_text(
        text,
        blocked_patterns=blocked_patterns_union(policies_dir),
        extra_exclusions=pack_exclusion_keywords(policies_dir),
    )


def screen_pref(text: str, policies_dir: str | Path = "policies") -> TextCheck:
    """classify_pref plus the intent screen (prefcheck_semantic, 2026-10-16): what
    the API uses for the composer's check and for adding. It can only ESCALATE
    classify_pref's verdict, and when the model can't run a clean verdict becomes
    warn. classify_pref stays the offline, deterministic floor."""
    from resume_screener.policy import blocked_patterns_union
    from resume_screener.prefcheck_semantic import screen_texts

    return screen_texts(
        [text], stage="preferences",
        blocked_patterns=blocked_patterns_union(policies_dir),
        extra_exclusions=pack_exclusion_keywords(policies_dir),
    )[0]


def _use_db() -> bool:
    """True when preferences live in the database (PREFS_BACKEND=db). Monkeypatched
    in tests; the default (jsonl) keeps the zero-infra file store."""
    from resume_screener.config import load_db_settings

    return load_db_settings().prefs_backend.strip().lower() == "db"


def _append(record: dict, log_path: str | Path = PREFERENCES_LOG) -> dict:
    if _use_db():
        from resume_screener.db.repositories import preferences as prefs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            prefs_repo.upsert(session, record)
        return record
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _fold(log_path: str | Path = PREFERENCES_LOG) -> dict[str, dict]:
    """Latest line per pref_id (corrupt lines skipped)."""
    if _use_db():
        from resume_screener.db.repositories import preferences as prefs_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            return prefs_repo.fold(session)
    path = Path(log_path)
    if not path.exists():
        return {}
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("pref_id"):
            latest[rec["pref_id"]] = rec
    return latest


def _display_status(rec: dict) -> str:
    """Stored status, except an approved pref past its review_due reads 'rereview',
    and a legacy 'active' one (live without sign-off, before 2026-10-16) reads
    'pending' — the one read-time seam both stores and every consumer pass through."""
    if rec.get("status") == STATUS_ACTIVE:
        return STATUS_PENDING
    if rec.get("status") == STATUS_APPROVED:
        due = rec.get("review_due")
        if due and due < _now():
            return STATUS_REREVIEW
    return rec.get("status", "")


def list_prefs(owner: str | None = None, *, log_path: str | Path = PREFERENCES_LOG) -> list[dict]:
    """Folded preferences (latest per id), newest-first, each with a computed
    `display_status`. `owner=None` returns everyone's (the approval queue reads
    this); an email scopes to that owner (the "my preferences" page)."""
    out = []
    for rec in _fold(log_path).values():
        if owner is not None and rec.get("owner") != owner:
            continue
        rec = {**rec, "display_status": _display_status(rec)}
        out.append(rec)
    out.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return out


def applies(rec: dict) -> bool:
    """Whether a folded preference may move a score: approved, and not due for
    re-review. The ONE rule the screening run and the preflight signature share."""
    return _display_status(rec) == STATUS_APPROVED


def own_pref_ids(pref_ids: list[str], approver: str, *,
                 log_path: str | Path = PREFERENCES_LOG) -> list[str]:
    """The ids among `pref_ids` that the approver owns — which they may not approve."""
    folded = _fold(log_path)
    who = approver.strip().lower()
    return [pid for pid in pref_ids
            if str((folded.get(pid) or {}).get("owner", "")).strip().lower() == who]


def texts_by_id(*, log_path: str | Path = PREFERENCES_LOG) -> dict[str, str]:
    """pref_id -> the preference's text, for readers that show events (the event
    spine carries ids, never preference text)."""
    return {pid: str(rec.get("text") or "") for pid, rec in _fold(log_path).items()}


def get_pref(pref_id: str, *, log_path: str | Path = PREFERENCES_LOG) -> dict | None:
    rec = _fold(log_path).get(pref_id)
    if rec is None:
        return None
    return {**rec, "display_status": _display_status(rec)}


def add_preference(
    owner: str, text: str, *, owner_name: str | None = None, actor_role: str = "",
    policies_dir: str | Path = "policies", log_path: str | Path = PREFERENCES_LOG,
    check: TextCheck | None = None,
) -> dict:
    """Add a new preference. Every attempt is recorded, blocked ones included.
    Classification decides the landing status: ok and warn -> pending (HR Head
    sign-off), blocked -> blocked (no approval). `check` is the verdict the caller
    already reached (the API passes the fairness screen's merged verdict); None
    classifies here, offline."""
    check = check or classify_pref(text, policies_dir)
    status = _LEVEL_TO_STATUS[check.level]
    record = {
        "pref_id": uuid.uuid4().hex[:12],
        "owner": owner,
        "owner_name": owner_name,
        "text": text.strip(),
        "action": "add",
        "status": status,
        "classification": check.model_dump(),
        "cap_points": _pref_cap(),
        "approved_by": None,
        "approved_at": None,
        "review_due": None,
        "actor": owner,
        "actor_role": actor_role,
        "ts": _now(),
    }
    _append(record, log_path)
    summary = ("Added a preference — needs HR Head sign-off" if status == STATUS_PENDING
               else f"Added a preference — {status}")
    # How the verdict was reached rides on the event (2026-10-16, pen-test RS-01): what
    # the phrase lists found, what the intent screen found and whether it ran. Never
    # the text — the preference store is the record of that, and the Audit Explorer
    # resolves it at read time (texts_by_id).
    emit_event("pref_add", actor=owner, actor_role=actor_role, summary=summary,
               data={"pref_id": record["pref_id"], "level": check.level,
                     "deterministic_level": check.deterministic_level or check.level,
                     "semantic_level": check.semantic_level, "semantic": check.semantic,
                     "basis_ids": list(check.basis_ids)})
    if status == STATUS_PENDING:
        emit_event("pref_review_requested", actor=owner, actor_role=actor_role,
                   summary="Preference needs HR Head sign-off",
                   data={"pref_id": record["pref_id"], "owner": owner})
    return {**record, "display_status": _display_status(record)}


def _rewrite(base: dict, *, action: str, status: str, actor: str, actor_role: str,
             log_path: str | Path, **extra) -> dict:
    """Append a new transition line carrying `base` forward with the changes."""
    record = {**base, "action": action, "status": status,
              "actor": actor, "actor_role": actor_role, "ts": _now()}
    record.pop("display_status", None)
    record.update(extra)
    _append(record, log_path)
    return record


def approve(pref_ids: list[str], *, approver: str, approver_role: str = "",
            log_path: str | Path = PREFERENCES_LOG) -> list[dict]:
    """Bulk-approve pending / re-review preferences. Stamps approved_by/at +
    review_due (+6 months). Blocked/withdrawn/declined prefs are skipped, and so is
    the approver's own — nobody signs off their own preference (the route refuses
    with a sentence; this is the rule held where it cannot be bypassed)."""
    folded = _fold(log_path)
    approved = []
    who = approver.strip().lower()
    for pref_id in pref_ids:
        base = folded.get(pref_id)
        if base is None:
            continue
        if str(base.get("owner", "")).strip().lower() == who:
            continue
        if _display_status(base) not in (STATUS_PENDING, STATUS_REREVIEW):
            continue
        now = _now()
        rec = _rewrite(base, action="approve", status=STATUS_APPROVED,
                       actor=approver, actor_role=approver_role, log_path=log_path,
                       approved_by=approver, approved_at=now,
                       review_due=_add_months(now, _review_months()))
        emit_event("pref_approve", actor=approver, actor_role=approver_role,
                   summary="Approved a preference",
                   data={"pref_id": pref_id, "owner": base.get("owner")})
        approved.append({**rec, "display_status": _display_status(rec)})
    return approved


def decline(pref_id: str, *, note: str, approver: str, approver_role: str = "",
            log_path: str | Path = PREFERENCES_LOG) -> dict | None:
    base = _fold(log_path).get(pref_id)
    if base is None or _display_status(base) not in (STATUS_PENDING, STATUS_REREVIEW):
        return None
    rec = _rewrite(base, action="decline", status=STATUS_DECLINED,
                   actor=approver, actor_role=approver_role, log_path=log_path,
                   decline_note=note)
    emit_event("pref_decline", actor=approver, actor_role=approver_role,
               summary="Declined preference", data={"pref_id": pref_id,
               "owner": base.get("owner"), "note": note})
    return {**rec, "display_status": _display_status(rec)}


def withdraw(pref_id: str, *, owner: str, actor_role: str = "",
             log_path: str | Path = PREFERENCES_LOG) -> dict | None:
    """Owner withdraws their own preference. Returns None if it isn't theirs."""
    base = _fold(log_path).get(pref_id)
    if base is None or base.get("owner") != owner:
        return None
    rec = _rewrite(base, action="withdraw", status=STATUS_WITHDRAWN,
                   actor=owner, actor_role=actor_role, log_path=log_path)
    emit_event("pref_withdraw", actor=owner, actor_role=actor_role,
               summary="Withdrew a preference", data={"pref_id": pref_id})
    return {**rec, "display_status": _display_status(rec)}
