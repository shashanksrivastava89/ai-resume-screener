"""Review-page view builders: turn a run's per-candidate JSON into the ranked
list and detail-drawer shapes the React Review page renders.

Recruiter language only — no layer numbers, signal ids, weights or hashes leak
into these dicts (that mapping is the design's §B3 copy rule)."""

from __future__ import annotations

from typing import Any

from resume_screener import findings as findings_mod
from resume_screener import location_context
from resume_screener.api import service
from resume_screener.models import Requisition
from resume_screener.scoring import band_label, rank_key

MATCH_GLYPH = {"full": "✓", "partial": "~", "none": "✗"}

# Layer 1 stamps this exact prefix into reasons for every failed hard gate —
# the run-time truth we surface (robust even if the checklist was edited since).
_GATE_REASON_PREFIX = "Hard requirement not met: "


def _failed_gates(layer1: dict) -> list[str]:
    """The failed gate TEXTS for one candidate, from the run's stored reasons."""
    return [r[len(_GATE_REASON_PREFIX):] for r in layer1.get("reasons", [])
            if r.startswith(_GATE_REASON_PREFIX)]


def _rank_key(rec: dict) -> tuple[int, int, float, str]:
    """The stored record's place in the list — the rule lives in `scoring.rank_key`,
    which the run's own reports use too."""
    s = rec["score"]
    return rank_key(s["fit_band"], s["final_score"], s["candidate_id"],
                    eligible=bool((s.get("layer1") or {}).get("eligible", True)))


def _chips(rec: dict, limit_each: int = 2) -> list[dict]:
    """Up to 2 strength chips (met requirements, green) + up to 2 watch-out chips
    (gaps, neutral) prefixed 'Partly shown:' / 'Missing:' — as in the design."""
    findings = rec["findings"]["findings"]
    missing = set(rec["score"]["layer1"].get("must_haves_missing", []))
    strengths = [f for f in findings if f["match"] == "full"][:limit_each]
    others = sorted(
        (f for f in findings if f["match"] != "full"),
        key=lambda f: (f["requirement_id"] not in missing, f["match"] == "partial"),
    )[:limit_each]
    chips = [{"text": f["requirement_text"], "kind": "strength"} for f in strengths]
    for f in others:
        prefix = "Partly shown: " if f["match"] == "partial" else "Missing: "
        chips.append({"text": prefix + f["requirement_text"], "kind": "watchout"})
    return chips


def _has_flag(rec: dict) -> bool:
    s = rec["score"]
    if s.get("confidence") == "low":
        return True
    critic = s.get("critic")
    if critic:
        verdicts = critic.get("verdict_per_adjustment", {})
        if any(v != "pass" for v in verdicts.values()) or critic.get("overall_flags"):
            return True
    return bool(rec.get("suspended_adjustments"))


def _decision_counts(decisions: dict[str, dict], total: int) -> dict[str, int]:
    """{forward, discuss, not, awaiting} over the attached candidates."""
    return {
        "forward": sum(1 for d in decisions.values() if d["action"] == "forward"),
        "discuss": sum(1 for d in decisions.values() if d["action"] == "discuss"),
        "not": sum(1 for d in decisions.values() if d["action"] == "not"),
        "awaiting": total - len(decisions),
    }


def decision_counts_for(req: Requisition) -> dict[str, int]:
    """The Review payload's decision_counts, standalone — shared by the decision
    routes so a single decision's response lets the client reconcile its counts
    without refetching the whole review. Counted over ATTACHED candidates only,
    the exact math build_review uses."""
    cfg = service.config()
    attached = service.batch_candidate_ids(cfg, req)
    records = [r for r in service.read_candidate_records(req.req_id)
               if r["score"]["candidate_id"] in attached]
    decisions = {cid: d for cid, d in service.role_decisions(req.req_id).items()
                 if cid in attached}
    return _decision_counts(decisions, len(records))


# run_meta stages whose failures we translate into a plain recruiter sentence; the
# raw message from `validate` is already recruiter-facing (ingest.single_resume_problem).
_STAGE_NOTE = {
    "validate": "",  # message is already plain
    "extract": "The file couldn't be read (it may be scanned, encrypted or corrupt).",
}


def _skipped_files(meta: dict) -> list[dict]:
    """[{file, reason}] for each file the run couldn't score — so Review can tell the
    recruiter why a file produced no candidate instead of dropping it silently."""
    out: list[dict] = []
    for err in meta.get("errors", []) or []:
        stage = err.get("stage", "")
        note = _STAGE_NOTE.get(stage)
        if stage == "validate":
            # stored as "NotASingleResumeError: <plain message>" — show just the message
            raw = err.get("error", "")
            reason = raw.split(": ", 1)[1] if ": " in raw else raw
        elif note:
            reason = note
        else:
            reason = "This file couldn't be screened."
        out.append({"file": err.get("file", "a file"),
                    "reason": reason or "This file couldn't be screened."})
    return out


def _verdict_without_gate_clause(rationale: str, covered: int) -> str:
    """The drawer's one-sentence verdict, minus the gate list the gate block already
    shows. Everything AFTER the gate clause (coverage, must-have caps) is kept; when
    the clause was the whole sentence, say what the checklist showed instead — a
    candidate who misses a gate still has a coverage story worth a line."""
    text = (rationale or "").strip()
    if not text.startswith("Not a fit (gate): "):
        return text
    rest = text[len("Not a fit (gate): "):]
    # Sentences after the gate list start at the first ". " that follows it.
    tail = rest.split(". ", 1)[1].strip() if ". " in rest else ""
    return tail or f"Covers {covered}% of the checklist. The unmet hard requirements are listed above."


def _span_view(spans: list[dict]) -> list[dict]:
    """The dated role lines behind a length-of-experience finding, NEWEST FIRST, each
    carrying its own length and the title held in it.

    Newest first because that is how a résumé is written and how a recruiter reads one
    (2026-09-11): the current job is what they are hiring against, and it was at the
    bottom of the journey. The ordering and the arithmetic are done HERE, in Python,
    for the same reason the total is: a résumé date is read in exactly one place in this
    codebase. The drawer draws a journey out of these rows; it never parses "2019" or
    "present" itself. Lines whose dates we cannot read keep the model's order at the end
    — the quote is still verified and still worth showing, we just cannot place it on a
    timeline."""
    rows: list[tuple[tuple[int, int, int], dict]] = []
    for i, sp in enumerate(spans):
        start, end = sp.get("start", ""), sp.get("end", "")
        first, _last = findings_mod.span_bounds(start, end)
        rows.append((
            # Datable rows first, latest start at the top; ties and undatable rows keep
            # the model's own order, which follows the résumé.
            (0, -first, i) if first is not None else (1, 0, i),
            {
                "quote": sp.get("quote", ""),
                "title": sp.get("title", "") or "",
                "start": start,
                "end": end,
                "months": findings_mod.span_months(start, end),
                "ongoing": findings_mod.span_ongoing(end),
                # p38: every dated job comes back; the ones this requirement is not
                # about are shown for context, muted, with the model's reason. An older
                # artifact only ever held counted spans, hence the default.
                "counted": sp.get("counted", True) is not False,
                "why_not": sp.get("why_not", "") or "",
            },
        ))
    rows.sort(key=lambda row: row[0])
    return [row[1] for row in rows]


# The marker verify_findings writes when it refuses a quote. It stays in the stored
# note (the confidence rating counts it), but a reviewer should never READ it: the
# drawer shows the model's own words plus a plain sentence about what happened.
_DOWNGRADE = "Downgraded:"


def _plain_note(note: str) -> tuple[str, bool]:
    """(what to show, was the AI's own evidence refused?).

    "Downgraded: the AI's quote isn't in the résumé word for word" is a fact about
    OUR check, not about the candidate — and reading it next to a red ✗ suggests the
    person is missing something they may well have. So the drawer keeps the model's
    reasoning and marks the row as needing a human look instead."""
    if _DOWNGRADE not in (note or ""):
        return note or "", False
    kept = (note or "").split(_DOWNGRADE)[0].strip().strip("|").strip()
    return kept, True


def _partial_gates(rec: dict, gate_ids: set[str]) -> list[str]:
    """Unmet hard gates the résumé gave PARTIAL evidence for. Layer 1 passes a gate
    only on "full" (§5.2), which is right for a machine — but "close, and not
    counted" is different from "nothing there", and only the second is settled. The
    reviewer sees which is which."""
    return [f["requirement_text"] for f in (rec.get("findings", {}).get("findings") or [])
            if f.get("requirement_id") in gate_ids and f.get("match") == "partial"]


def _attach_location_context(req: Requisition, cards: list[dict],
                             records_by_id: dict[str, dict]) -> None:
    """Add the non-scoring location fields to each card, in place.

    WHAT IS FROZEN AND WHAT IS DERIVED. The expensive, evidence-bearing half — what the
    résumé actually STATES — is read once at screening time and stored in the candidate's
    record. That is the half that mattered for speed: the original version parsed every
    résumé off the Azure Files share on every Review load, 26 SECONDS for 42 candidates.
    The arithmetic half — two table lookups and a haversine — is re-derived HERE, from
    the stored place against the requisition's CURRENT location, because otherwise:

      * a city added to the places table never reaches the 300 candidates already
        screened against it (a Budapest requisition stayed 100% "unknown" until someone
        re-ran a screening job to fix a missing row — 2026-09-11), and
      * editing the job's location leaves every stored distance measured from the OLD
        city while the note names the new one, which is worse than unknown.

    It costs one table load per Review, not per candidate. A record written before the
    feature has no `location`, so its card reports "unknown" — never "far away".
    """
    place = req.work_location.place if req.work_location else ""
    radius = req.work_location.radius_km if req.work_location else None
    table = location_context.places()
    origin = table.resolve(place) if place else None
    job_known = origin is not None
    for card in cards:
        stored = (records_by_id.get(card["candidate_id"], {}) or {}).get("location") or {}
        stated = (location_context.LocationContext(**stored).stated if stored else None)
        km = table.km_from((origin[1], origin[2]) if origin else None, stated)
        view = location_context.LocationContext(
            job_place=place, stated=stated, distance_km=km, resolved=km is not None)
        card["location_place"] = place
        card["location_stated"] = stated
        card["location_km"] = km
        card["location_resolved"] = km is not None
        # `within` is a VIEW over the number, computed here so the client never
        # re-implements the comparison: True/False inside/outside the radius, None when
        # the distance is unknown — and None must never be filtered out.
        card["location_within"] = (km <= radius) if (km is not None and radius is not None) else None
        card["location_note"] = view.note(radius, job_known=job_known) if place else ""


def build_review(req: Requisition, blind: bool = False,
                 hm_view: bool = False) -> dict[str, Any]:
    """The whole Review payload: readiness, run summary, ranked candidate cards.

    hm_view (2026-07-19): the hiring-manager variant — candidates appear ONLY
    after the recruiter shares the shortlist, and then AS OF the share's frozen
    snapshot (never a live sync — the recruiter sends updates explicitly).
    Move forward / Discuss are the working set; Not-a-fit and not-yet-decided
    ride along minimized so the HM can disagree or look ahead. Counts are
    recomputed on the shared set so nothing about the unshared batch leaks."""
    meta = service.read_run_meta(req.req_id)
    cfg = service.config()
    attached = service.batch_candidate_ids(cfg, req)
    # A run whose scored résumés are all gone is stale → treat the role as not
    # yet screened (so Review never shows candidates that aren't on the batch).
    records = sorted(
        (r for r in service.read_candidate_records(req.req_id)
         if r["score"]["candidate_id"] in attached),
        key=_rank_key, reverse=True,
    ) if meta else []
    # Files the run refused to score (an un-split Bulk Print, a merged packet, an
    # unreadable file) — surfaced so a recruiter sees WHY a file produced no
    # candidate instead of it silently vanishing.
    skipped = _skipped_files(meta) if meta else []
    if not meta or not records:
        return {"ready": False, "req_id": req.req_id, "title": service.display_title(req),
                "location_display": service.location_display(req),
                "skipped": skipped}

    private_map = None if blind else service._private_map(req.req_id)
    decisions = {cid: d for cid, d in service.role_decisions(req.req_id).items()
                 if cid in attached}
    # Raw records (once) — reviewer attribution AND the carry-forward reconfirm
    # state (a decision carried onto rescored candidates gets a badge).
    decision_records = {cid: d for cid, d in service.role_decision_records(req.req_id).items()
                        if cid in attached}
    # Who recorded each decision/note — surfaced on the card so any TA opening the
    # requisition sees whose note it is (the note itself is the decision reason).
    reviewer_of = {cid: d.reviewer for cid, d in decision_records.items()}
    # HM takes for this run (P41): input, keyed by candidate — shown as a chip,
    # never a decision. Empty until an HM sends takes / the portal is on.
    from resume_screener import hm_takes
    takes = hm_takes.takes_by_candidate(req.req_id, str(meta.get("run_id", "")))

    # Comments (2026-07-19): one read for the whole payload; cards carry counts
    # (the thread itself rides on the detail drawer). Counts are LIVE for everyone
    # now (2026-07-20) — the HM sees recruiter comments as soon as they're posted.
    from resume_screener import comments as comments_mod
    all_comments = comments_mod.comments_by_candidate(req.req_id)

    # Gate total from the role's checklist (same latest-version source the
    # drawer's must/gate tags use); per-candidate failures from the run record.
    rubric = service.rubric_for(req)
    n_gates = sum(1 for r in rubric.requirements if r.kind == "hard_gate") if rubric else 0
    gate_ids = {r.id for r in rubric.requirements if r.kind == "hard_gate"} if rubric else set()
    # id -> text for the must-have gap column (CSV export); a row edited out of
    # the checklist since the run falls back to its raw id — honest, not hidden.
    text_of = {r.id: r.text for r in rubric.requirements} if rubric else {}

    # One scoring model for the whole payload (brief_math re-adds to the stored
    # final with its preference cap + clamp; the loader is file-state cached).
    from resume_screener.api import score_math as score_math_mod
    from resume_screener.scoring_model import load_scoring_model

    scoring_model = load_scoring_model()

    cards = []
    for rank, rec in enumerate(records, start=1):
        s = rec["score"]
        cid = s["candidate_id"]
        name = f"Candidate {rank}" if blind else service.candidate_name(req.req_id, cid, private_map)
        decided = decisions.get(cid)
        eligible = bool(s["layer1"].get("eligible", True))
        failed = _failed_gates(s["layer1"])
        cards.append({
            "rank": rank,
            "candidate_id": cid,
            "name": name,
            "band": s["fit_band"],
            "band_label": band_label(s["fit_band"], eligible),
            "score": round(s["final_score"]),
            "confidence": s["confidence"],
            "flag": _has_flag(rec),
            "chips": _chips(rec),
            # `partial` (2026-09-07) is the subset of `failed` where the résumé showed
            # SOMETHING — the machine needs "full" to pass a gate, so a partial is
            # precisely the case a human should judge. Surfaced, never auto-passed.
            "gates": {"total": max(n_gates, len(failed)), "failed": failed,
                      "partial": _partial_gates(rec, gate_ids)},
            "must_have_gaps": [text_of.get(rid, rid)
                               for rid in s["layer1"].get("must_haves_missing", [])],
            "decided": decided["action"] if decided else None,
            "decided_reason": decided["reason"] if decided else None,
            "decided_ts": decided["ts"] if decided else None,
            "decided_by": reviewer_of.get(cid) or None,
            # carried forward from an earlier run whose scores have since moved →
            # show a "decided under previous scores · reconfirm" badge.
            "decided_reconfirm": service.decision_reconfirm(
                decision_records.get(cid), meta)["needs_reconfirm"],
            "hm_take": ({"take": takes[cid]["take"], "hm_name": takes[cid].get("hm_name", "")}
                        if cid in takes else None),
            "comments": len(all_comments.get(cid, [])),
            # The score tip's compact arithmetic (base + extras + preferences
            # → final) — zero extra reads, the record is in hand.
            "math": score_math_mod.brief_math(rec, scoring_model),
        })

    # The share state rides on every payload (the TA's Shortlist tab shows it;
    # the HM's whole view hangs on it). hm_updates = what changed since the
    # last share — the TA's "Send updates" button reads it.
    share = service.hm_share_record(req.req_id, meta)
    hm_updates = service.hm_share_updates(cfg, req, share)
    if hm_view:
        if share is None:
            return {"ready": True, "req_id": req.req_id, "title": req.role_title,
                    "req_code": service.icims_of(req),
                    "run_id": meta.get("run_id"),
                    "location_display": service.jurisdiction_display(
                        meta.get("jurisdiction", req.job_location)),
                    "meta": {"screened": 0, "rubric": None, "when": meta.get("started_utc")},
                    "counts": {"strong": 0, "possible": 0, "not_a_fit": 0, "needs_look": 0,
                               "gates_passed": 0, "gate_not_met": 0, "total": 0},
                    "decision_counts": {"forward": 0, "discuss": 0, "not": 0, "awaiting": 0},
                    "cards": [], "skipped": [], "staleness": None,
                    "hm_shared": False, "hm_share": None,
                    "hm_updates": None, "batch_quality": None}
        # The HM sees the shared set with DECISIONS as of the last share
        # (2026-07-21): the recruiter's later decision changes reach the HM only
        # when they "send updates", so the recruiter controls the shortlist the
        # HM reviews. COMMENTS, by contrast, are LIVE (the card counts above come
        # from the live thread) — near-real-time, no manual send.
        snap = {row.get("candidate_id"): row for row in share.get("snapshot") or []}
        cards = [c for c in cards if c["candidate_id"] in snap]
        for c in cards:
            row = snap[c["candidate_id"]]
            c["decided"] = row.get("action")
            c["decided_reason"] = row.get("reason") or None
            c["decided_ts"] = row.get("ts")
            c["decided_by"] = row.get("by")
            c["decided_reconfirm"] = False  # reconfirm is a recruiter concept
        decisions = {cid: {"action": row.get("action"), "reason": row.get("reason", ""),
                           "ts": row.get("ts")}
                     for cid, row in snap.items()
                     if row.get("action") and any(c["candidate_id"] == cid for c in cards)}
        skipped = []  # batch-file mechanics stay with the recruiter

    # Counts recomputed from the attached records (not the stored run_meta).
    counts = {
        "strong": sum(1 for c in cards if c["band"] == "Strong"),
        "possible": sum(1 for c in cards if c["band"] == "Possible"),
        "not_a_fit": sum(1 for c in cards if c["band"] == "Not a fit"),
        "needs_look": sum(1 for c in cards if c["flag"]),
        "gates_passed": sum(1 for c in cards if not c["gates"]["failed"]),
        "gate_not_met": sum(1 for c in cards if c["gates"]["failed"]),
        "total": len(cards),
    }
    decision_counts = _decision_counts(decisions, len(cards))

    # P51: were these scores produced under older published rules? One payload-
    # level block (every card shares the run, so it drives every row's icon).
    drift = service.config_drift(cfg, req, meta)
    staleness = None
    if any(drift["changed"].values()):
        mode = service.staleness_mode(drift, bool(decisions))
        staleness = {
            "mode": {"stale_locked": "locked", "stale_soft": "soft",
                     "auto_rescore": "auto"}[mode],
            "summary": service._stale_summary(
                [k for k, v in drift["changed"].items() if v]),
            "details": drift["details"],
        }
    # Location context (2026-08-20). Attached HERE, at the view layer, and never in the
    # scoring pipeline: it is read from the ORIGINAL résumé (addresses are redacted
    # before anything scores), it produces no number, and `commute_or_address` is a
    # floor never-scored topic. Purely a lens for the human reviewing the list.
    _attach_location_context(req, cards, {r['score']['candidate_id']: r for r in records})

    return {
        "ready": True,
        "req_id": req.req_id,
        "title": req.role_title,
        "req_code": service.icims_of(req),  # the iCIMS id, for the CSV export
        "run_id": meta.get("run_id"),
        "location_display": service.jurisdiction_display(meta.get("jurisdiction", req.job_location)),
        # The requisition's criterion, so the filter's label is generated from data
        # rather than assembled in JSX. null when no location is set; `place_known`
        # tells the filter whether distances are available at all.
        "work_location": service.work_location_view(req),
        "jurisdiction": meta.get("jurisdiction"),
        "meta": {
            "screened": counts["total"],
            "rubric": meta.get("versions", {}).get("rubric"),
            "when": meta.get("started_utc"),
        },
        "counts": counts,
        "decision_counts": decision_counts,
        "cards": cards,
        "skipped": skipped,
        "staleness": staleness,
        # 2026-07-19: the share-with-HM state for the CURRENT run + what changed
        # since (drives the TA's "Send updates" button; null on the HM's view).
        # The raw snapshot stays server-side — the client only needs who/when.
        "hm_shared": share is not None,
        "hm_share": ({"shared_by": share.get("shared_by"), "ts": share.get("ts"),
                      "run_id": share.get("run_id")} if share else None),
        "hm_updates": None if hm_view else hm_updates,
        # The batch-level quality read — recruiter surfaces only (a batch read
        # would leak the unshared batch to an HM).
        "batch_quality": None if hm_view else service.batch_quality(records),
    }


def build_detail(req: Requisition, candidate_id: str, blind: bool = False,
                 viewer: str = "", hm_view: bool = False) -> dict[str, Any] | None:
    """The slide-in detail drawer for one candidate. hm_view (2026-07-19): the
    decision and the comment thread are shown AS OF the last share — the HM's
    own comments always show; the recruiter's appear once sent."""
    rec = next((r for r in service.read_candidate_records(req.req_id)
                if r["score"]["candidate_id"] == candidate_id), None)
    if rec is None:
        return None

    # The comment thread (2026-07-19) — every entry carries its own author, so
    # attribution can never drift to whoever recorded the decision. The thread is
    # LIVE for the HM now (2026-07-20): once a candidate is shared (the route
    # enforces membership), the HM sees the recruiting team's comments as they
    # land, not a frozen snapshot.
    from resume_screener import comments as comments_mod
    share = service.hm_share_record(req.req_id) if hm_view else None  # HM decision snapshot
    thread = comments_mod.list_comments(req.req_id, candidate_id)  # LIVE for everyone
    thread_reactions = comments_mod.reactions_by_comment([c["comment_id"] for c in thread])

    # The viewer's PRIVATE self-note (2026-07-20) — recruiter-only, survives a
    # re-screen; the HM never has one (their route is recruiter-gated).
    self_note = None
    if viewer and not hm_view:
        from resume_screener import self_notes
        self_note = self_notes.get_note(req.req_id, candidate_id, viewer)

    s = rec["score"]
    rubric = service.rubric_for(req)
    kind_of = {r.id: r.kind for r in rubric.requirements} if rubric else {}
    text_of = {r.id: r.text for r in rubric.requirements} if rubric else {}

    reqs = [{
        "label": f["requirement_text"],
        "match": f["match"],
        "glyph": MATCH_GLYPH.get(f["match"], "?"),
        "must": kind_of.get(f["requirement_id"]) == "must_have",
        "gate": kind_of.get(f["requirement_id"]) == "hard_gate",
        "evidence": f.get("evidence") or "",
        "note": _plain_note(f.get("note") or "")[0],
        # True = the AI judged this met but its quote didn't check out against the
        # résumé, so the score doesn't count it. That is OUR check being careful,
        # not the candidate falling short — the reviewer settles it.
        "unverified": _plain_note(f.get("note") or "")[1],
        # The dated role lines a LENGTH-of-experience requirement was worked out from,
        # each one verified against the résumé, plus the total OUR code made of them
        # (2026-09-07). A reviewer can check the addition instead of taking it on
        # trust — which is the whole point of showing it.
        "spans": _span_view(f.get("experience_spans") or []),
        "months": f.get("experience_months"),
        # Every dated job, counted or not (p38). None on an older artifact — its spans
        # were all counted ones, so the drawer shows no second total.
        "months_total": f.get("experience_months_total"),
    } for f in rec["findings"]["findings"]]

    # Context notes = surviving Layer-2 adjustments in plain language. Suspended
    # ones are marked + removed from the score; FLAGGED ones stay in the score but
    # are marked "worth a look" — so the "N flagged for a closer look" banner can
    # point at exactly which notes it means. A flag is about REASONING QUALITY
    # (thin evidence, stretched rationale, double-counting), NOT the sign of the
    # points — a positive nudge can be flagged too.
    suspended_ids = {a["signal_id"] for a in rec.get("suspended_adjustments", [])}
    critic_verdicts = (s.get("critic") or {}).get("verdict_per_adjustment", {}) or {}
    notes = []
    if s.get("layer2"):
        for a in s["layer2"]["adjustments"]:
            sid = a["signal_id"]
            notes.append({
                "signal": _humanize_signal(sid, text_of),
                "points": round(a["points"]),
                "evidence": a.get("evidence", ""),
                "rationale": a.get("rationale", ""),
                "suspended": sid in suspended_ids,
                # flagged (not suspended) — stays in the score, human should glance
                "flagged": critic_verdicts.get(sid) == "flag" and sid not in suspended_ids,
            })

    # Preference notes (P40): the applied ±2 HR-preference nudges, rendered under
    # Context notes. "Your preference" when the viewer owns it, else "{owner}'s".
    pref_notes = []
    for p in s.get("preference_adjustments", []):
        owner = p.get("owner", "")
        pref_notes.append({
            "text": p.get("text", ""),
            "owner_label": "Your preference" if owner and owner == viewer else f"{owner}'s preference",
            "points": round(p.get("points", 0)),
            "evidence": p.get("evidence") or "",
        })

    # The drawer's gate block. One line per unmet gate (2026-09-07: it used to be a
    # single paragraph that ran the gate texts, the rule and the caveat together —
    # unreadable at three gates). PARTIAL evidence is called out separately: the
    # candidate showed something, layer 1 requires "full", so that is exactly the
    # case a human should judge rather than a machine. Computed BEFORE the flag
    # block, which raises a partial gate as one of its "closer look" reasons.
    eligible = bool(s["layer1"].get("eligible", True))
    failed_gates = _failed_gates(s["layer1"])
    gate_items = [{
        "text": r["label"],
        # Three different situations, and only the last is settled:
        #   unverified — the AI judged it met, its quote didn't check out
        #   partial    — the résumé shows some of it, short of the bar
        #   none       — nothing in the résumé to judge it by
        "state": ("unverified" if r["unverified"]
                  else "partial" if r["match"] == "partial" else "none"),
        "evidence": r["evidence"],
        "note": r["note"],
    } for r in reqs if r["gate"] and r["match"] != "full"]
    n_partial = sum(1 for g in gate_items if g["state"] in ("partial", "unverified"))
    n_unverified = sum(1 for g in gate_items if g["state"] == "unverified")
    gate_warn = ""
    if failed_gates:
        n = len(failed_gates)
        gate_warn = f"{n} hard requirement{'s' if n != 1 else ''} not met"

    # The "Worth a closer look" block must explain EVERY reason the card's flag
    # (_has_flag) can fire — otherwise a flagged row opens a drawer with no visible
    # cause. Each reason is now an ITEM with somewhere to go (2026-09-07): the old
    # version pasted the critic's own sentence ("narrative mentions 'stable
    # multi-year stints' which risks proxying for…") and left the reader with no
    # idea what to do about it. `where` names a drawer section the client can jump
    # to; `flag_note` stays as the joined text for the HM view, the handoff bundle
    # and the MCP connector.
    flag_items: list[dict] = []

    def _flag(text: str, where: str = "", action: str = "") -> None:
        flag_items.append({"text": text, "where": where, "action": action})

    critic = s.get("critic")
    if critic:
        for raw in critic.get("overall_flags", []) or []:
            # The deterministic narrative screen has a known shape; give it the
            # action, not the machinery. Anything else is the critic model's own
            # sentence — shown as written, with the summary to check it against.
            if raw.startswith("Narrative touches a never-scored topic"):
                _flag("The AI's written summary uses wording that can stand in for "
                      "something we never score. The score itself is unaffected — read "
                      "the summary and judge whether it reads fairly.",
                      "verdict", "Read the summary")
            else:
                _flag(raw, "verdict", "Read the summary")
        verdicts = critic.get("verdict_per_adjustment", {}) or {}
        n_susp = sum(1 for v in verdicts.values() if v == "suspend")
        n_flag = sum(1 for v in verdicts.values() if v == "flag")
        if n_susp:
            _flag(f"{n_susp} score adjustment{'s' if n_susp != 1 else ''} "
                  f"{'were' if n_susp != 1 else 'was'} set aside by the fairness check "
                  f"and {'do' if n_susp != 1 else 'does'} not count towards the score.",
                  "score", "See what was set aside")
        if n_flag:
            _flag(f"{n_flag} score adjustment{'s' if n_flag != 1 else ''} "
                  f"{'are' if n_flag != 1 else 'is'} counted but worth your eye — the "
                  "reasoning is thin or stretched.",
                  "score", "See the flagged notes")
    if s.get("confidence") == "low":
        _flag("Parts of this résumé were hard to read, so the evidence behind these "
              "matches may be thinner than it looks.",
              "resume", "Open the résumé")
    n_close = n_partial - n_unverified
    if n_close:
        _flag(f"{n_close} hard requirement{'s' if n_close != 1 else ''} "
              f"{'are' if n_close != 1 else 'is'} part-way met — the résumé shows some of "
              "it, not all. A person decides this one, not the score.",
              "evidence", "Check the evidence")
    if n_unverified:
        _flag(f"The AI read {n_unverified} hard requirement{'s' if n_unverified != 1 else ''} "
              "as met, but could not point at the exact words in the résumé — so the score "
              "leaves it out. Read the résumé and decide.",
              "evidence", "Check the evidence")
    flagged = _has_flag(rec)
    if flagged and not flag_items:
        _flag("Worth a second look before you decide.", "evidence", "Check the evidence")
    flag_note = " ".join(item["text"] for item in flag_items)

    name = "This candidate" if blind else service.candidate_name(req.req_id, candidate_id)
    # The stated location, read from the same frozen artifact the cards use (never
    # re-parsed here). It leads the drawer header because it is the first thing a
    # recruiter checks about a person — and it is NON-SCORING throughout.
    loc_card: dict[str, Any] = {"candidate_id": candidate_id}
    _attach_location_context(req, [loc_card], {candidate_id: rec})
    covered = round(s["layer1"]["coverage_score"] * 100)
    n_reqs = len(reqs)
    n_met = sum(1 for r in reqs if r["match"] == "full")
    return {
        "candidate_id": candidate_id,
        "name": name,
        "band": s["fit_band"],
        "band_label": band_label(s["fit_band"], eligible),
        "gate_warn": gate_warn,
        # One entry per unmet hard gate, so the drawer can list them instead of
        # running them into a paragraph; `state` marks the partial ones.
        "gate_items": gate_items,
        "gate_rule": ("A hard requirement is pass or fail. Missing one puts this candidate "
                      "below everyone who meets them all. Nobody is turned down here — you "
                      "can still move them forward, with a reason."),
        "score": round(s["final_score"]),
        "confidence": s["confidence"],
        "sub": f"Covers {covered}% of the checklist · confidence {s['confidence']}",
        # The stored rationale leads with "Not a fit (gate): <every gate text>" for an
        # ineligible candidate — which the gate block above now says, one line each.
        # Reading the same list twice, once as a wall of semicolons, is what made this
        # screen hard (2026-09-07, item 4). The RECORD keeps the full sentence; only
        # this view drops the duplicated clause.
        "verdict": _verdict_without_gate_clause(s["summary_rationale"], covered),
        "flag": flagged,
        "flag_note": flag_note,
        "flag_items": flag_items,
        "strengths": [f["requirement_text"] for f in rec["findings"]["findings"]
                      if f["match"] == "full"],
        "watchouts": ([text_of.get(m, m) for m in s["layer1"].get("must_haves_missing", [])]
                      or [f["requirement_text"] for f in rec["findings"]["findings"]
                          if f["match"] == "none"][:4]),
        "req_summary": f"{n_met} of {n_reqs} met",
        "reqs": reqs,
        "notes": notes,
        "pref_notes": pref_notes,
        "hm_take": _hm_take_for(req.req_id, s, candidate_id),
        # The HM sees the shared-snapshot decision (refreshed on "send updates");
        # the recruiter sees the live decision + reconfirm state (2026-07-21).
        "decision": (_snapshot_decision(share, candidate_id) if hm_view
                     else _decision_with_reviewer(req.req_id, candidate_id)),
        "comments": [{"comment_id": c.get("comment_id"), "author": c.get("author"),
                      "author_name": c.get("author_name", ""),
                      "author_role": c.get("author_role", ""),
                      "text": c.get("text", ""), "ts": c.get("ts"),
                      "mentions": c.get("mentions", []),
                      "reactions": reaction_view(
                          thread_reactions.get(c["comment_id"], {}), viewer,
                          service.req_meta(req))}
                     for c in thread],
        "location_place": loc_card.get("location_place") or "",
        "location_stated": loc_card.get("location_stated"),
        "location_km": loc_card.get("location_km"),
        "location_note": loc_card.get("location_note") or "",
        "resume_media_type": _resume_media_type(req.req_id, candidate_id),
        # The viewer's own private note (recruiter-only), {text, ts} or null.
        "self_note": self_note,
        # P51: the full "How this score adds up" trace, as-of the run's stamped
        # rubric + scoring-model versions.
        "score_math": _score_math_safe(req, rec),
    }


def _score_math_safe(req: Requisition, rec: dict) -> dict | None:
    """build_score_math, degraded to None on any per-record read problem — the
    drawer's other sections must never be taken down by the math panel."""
    try:
        from resume_screener.api import score_math as score_math_mod

        return score_math_mod.build_score_math(req, rec, service.read_run_meta(req.req_id))
    except Exception:  # noqa: BLE001
        return None


def reaction_view(rmap: dict[str, list[str]], viewer: str,
                  req_meta: dict | None = None) -> list[dict]:
    """One comment's reactions for the client: [{emoji, count, mine, who}] where
    `who` are display NAMES (hover 'Bhavika, Rahul reacted'), `mine` marks the
    viewer's own so the pill highlights and toggles cleanly. `req_meta` lets an
    HM's reaction resolve to their real name. Emitted in the fixed palette order
    so pills don't reshuffle between renders."""
    from resume_screener import comments as comments_mod, names

    me = str(viewer or "").strip().lower()
    out: list[dict] = []
    for emoji in comments_mod.ALLOWED_REACTIONS:
        users = rmap.get(emoji) or []
        if not users:
            continue
        out.append({"emoji": emoji, "count": len(users),
                    "mine": any(str(u).strip().lower() == me for u in users),
                    "who": [names.display_name(u, req_meta=req_meta) for u in users]})
    return out


def _snapshot_decision(share: dict | None, candidate_id: str) -> dict | None:
    """The HM drawer's decision — from the share snapshot (refreshed on the
    recruiter's 'send updates'), never the live log."""
    if not share:
        return None
    row = next((r for r in share.get("snapshot") or []
                if r.get("candidate_id") == candidate_id), None)
    if not row or not row.get("action"):
        return None
    return {"action": row["action"], "reason": row.get("reason", ""),
            "ts": row.get("ts"), "reviewer": row.get("by")}


def _decision_with_reviewer(req_id: str, candidate_id: str) -> dict | None:
    """The candidate's current decision as {action, reason, ts, reviewer,
    needs_reconfirm} — the drawer shows who recorded the note (attribution) and
    whether it was carried forward from scores that have since moved."""
    ui = service.role_decisions(req_id).get(candidate_id)
    if ui is None:
        return None
    rec = service.role_decision_records(req_id).get(candidate_id)
    reconfirm = service.decision_reconfirm(rec, service.read_run_meta(req_id))
    return {**ui, "reviewer": rec.reviewer if rec else None,
            "needs_reconfirm": reconfirm["needs_reconfirm"]}


def _hm_take_for(req_id: str, score: dict, candidate_id: str) -> dict | None:
    """The assigned HM's take on this candidate for the run that produced `score`
    (P41) — {take, hm_name} or None. Read from the run's run_meta run_id."""
    from resume_screener import hm_takes

    meta = service.read_run_meta(req_id)
    run_id = str((meta or {}).get("run_id", ""))
    takes = hm_takes.takes_by_candidate(req_id, run_id)
    rec = takes.get(candidate_id)
    return {"take": rec["take"], "hm_name": rec.get("hm_name", "")} if rec else None


def _resume_media_type(req_id: str, candidate_id: str) -> str | None:
    """"pdf" / "docx" from the private-map path suffix — the drawer embeds PDFs
    inline; null when the original file is gone (nothing to preview)."""
    path = service.resume_path(req_id, candidate_id)
    if path is None:
        return None
    return path.suffix.lower().lstrip(".") or None


def _humanize_signal(signal_id: str, _text_of: dict) -> str:
    """Signal id -> plain phrase (no ids on screen). Falls back to a title-cased
    version of the id."""
    friendly = {
        "tenure_pattern_vs_stability": "Consistent tenure pattern",
        "preferred_domain_background": "Relevant domain background",
        "regulated_environment_exposure": "Regulated-environment exposure",
        "skill_recency": "Recent, current skills",
        "scope_trajectory": "Growing scope over time",
        "hands_on_currency": "Hands-on and current",
        "domain_transfer_distance": "Domain transfer distance",
        "certification_currency": "Current certifications",
        "language_evidence": "Relevant languages",
        "quota_attainment_evidence": "Quota attainment",
        "deal_profile_fit": "Deal-profile fit",
        "public_work_relevance": "Relevant public work",
    }
    return friendly.get(signal_id, signal_id.replace("_", " ").capitalize())
