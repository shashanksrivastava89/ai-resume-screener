"""Audit Explorer backend (P45): audit becomes queryable.

Every run, decision, rule change and requisition event — merged into one feed,
per-candidate timelines, a rules log, the retention/purge queue, and CSV reports.
Per Addendum 11 Part A2. VISIBILITY ONLY — nothing here changes or deletes data
(the one exception is placing/lifting a legal HOLD, which is itself only a status
flag that pauses a purge that never runs).

Guardrails baked in:
- every route requires the runs_audit capability (HR Head + IT);
- every route accepts region= and enforces the caller's region access (P38);
- EVERY handler emits an `audit_view` event (viewer, scope, query) — "viewing
  here is itself logged" — and those view events are EXCLUDED from the feed by
  default (a "Views" chip opts in), so the log doesn't drown in its own reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from resume_screener import events, feedback, governance, hm_takes, regionmap, retention
from resume_screener.api import ratelimit, service
from resume_screener.api.review import _failed_gates
from resume_screener.scoring import band_label

router = APIRouter(prefix="/api/audit")

# events.py kind -> feed filter chip. Views (reads) are opt-in; everything not
# otherwise classified is generic "Data" activity.
_VIEW_KINDS = {"audit_view", "req_history_view"}
_CLOSE_KINDS = {"req_close", "req_reopen", "req_snooze"}


def _pref_texts() -> dict[str, str]:
    from resume_screener import preferences

    try:
        return preferences.texts_by_id()
    except Exception:  # noqa: BLE001 — a store read must never break the explorer
        return {}


def _with_pref_text(ev: dict, summary: str, texts: dict[str, str]) -> str:
    """A preference event's summary with the preference's own words, resolved at
    read time from the store (the event line carries only the id), so the feed
    shows WHAT was added, approved or blocked and the search finds it."""
    pid = (ev.get("data") or {}).get("pref_id")
    text = texts.get(pid) if pid else None
    return f"{summary} — “{text}”" if text else summary


def _chip_for_event(kind: str) -> str:
    if kind in _VIEW_KINDS:
        return "Views"
    if kind.startswith("pref_"):
        return "Preference"
    if kind.startswith("checklist_"):
        return "Rules"
    if kind in _CLOSE_KINDS:
        return "Close"
    return "Data"


# --- guard + region enforcement ----------------------------------------------------


def audit_identity(request: Request) -> tuple[str, str]:
    """runs_audit capability gate (HR Head + IT), reusing app's dependency lazily
    so the message/behaviour can never drift from the rest of the API."""
    from resume_screener.api.app import _runs_audit

    return _runs_audit(request)


def _enforce_region(user: str, region: str | None) -> set[str] | None:
    """Return the caller's accessible region set (None = unrestricted) and 403 if a
    caller asks to scope to a region they can't see (P38)."""
    accessible = service.accessible_regions(user)
    if region and accessible is not None and region not in accessible:
        raise HTTPException(403, "You don't have access to that region.")
    return accessible


def _visible(item_region: str | None, region: str | None, accessible: set[str] | None) -> bool:
    """A feed/report item passes the region lens when: it's within the caller's
    access (global/region-less items always pass access), AND — if the caller chose
    a specific region= — it belongs to that region."""
    if accessible is not None and item_region is not None and item_region not in accessible:
        return False
    if region:
        return item_region == region
    return True


def _shows_verdict() -> bool:
    """May the current viewer see the AI's verdict? The audit routes gate on
    runs_audit, which Legal holds without verdict_view — so every surface here that
    carries a band or a score asks this (api/verdict.py is the rule)."""
    from resume_screener.api import verdict
    from resume_screener.api.app import _request_caps

    return verdict.allowed(_request_caps())


def _log_view(user: str, role: str, scope: str, query: dict) -> None:
    """Append the audit_view event — viewing the explorer is itself logged."""
    clean = {k: v for k, v in query.items() if v}
    events.emit_event("audit_view", actor=user, actor_role=role,
                      summary=f"Viewed {scope}" + (f" ({clean})" if clean else ""),
                      data={"scope": scope, "query": clean})


# --- shared context (req/run/region resolution) ------------------------------------


def _context(show_verdict: bool = True) -> tuple[dict, list[dict], dict]:
    """(req_id -> Requisition, run index newest-first, run_id -> run row) — the
    lookups the feed uses to attribute a decision/run/event to a requisition and
    thence a region. show_verdict=False leaves the band tally out of run rows."""
    cfg = service.config()
    reqs = {r.req_id: r for r in service.requisitions(cfg)}
    runs = service.list_runs(show_verdict=show_verdict)
    run_by_id = {r["run_id"]: r for r in runs if r.get("run_id")}
    return reqs, runs, run_by_id


def _region_of_req(reqs: dict, req_id: str | None) -> str | None:
    req = reqs.get(req_id or "")
    return regionmap.region_for_jurisdiction(req.job_location) if req else None


# --- GET /api/audit/feed -----------------------------------------------------------


def build_feed(q: str = "", kind: str | None = None,
               show_verdict: bool = True) -> list[dict]:
    """The merged newest-first feed: decisions (verbatim reasons) + governance audit
    (rule changes) + requisition/app events + screening-run starts. `audit_view`
    reads are excluded unless kind == 'Views'. show_verdict=False keeps the run
    rows' band tally out (the viewer may see decisions, not the AI's verdict)."""
    include_views = kind == "Views"
    reqs, runs, run_by_id = _context(show_verdict)
    items: list[dict] = []

    # 1) Decisions — the reviewer's verbatim reason.
    for d in feedback.load_decisions():
        run = run_by_id.get(d.run_id)
        req_id = run["req_id"] if run else None
        name = (service.candidate_name(req_id, d.candidate_id)
                if req_id else f"Candidate {d.candidate_id[:6]}")
        cleared = service._is_cleared(d)
        label = "Cleared" if cleared else service.ACTION_LABEL[service.action_from_decision(d)]
        summary = f"{name}: {label}" + (f" — {d.reason}" if d.reason and d.reason != label else "")
        items.append({
            "ts": d.ts, "kind": "Decision", "actor": d.reviewer or "",
            "summary": summary, "req_id": req_id, "candidate": name,
            "region": _region_of_req(reqs, req_id) if req_id
            else (regionmap.region_for_jurisdiction(run["jurisdiction"]) if run else None),
            "source": "decision",
        })

    # 2) Governance audit — rule/policy publishes + review sign-offs.
    for entry in governance.load_audit():
        rationale = entry.get("rationale")
        base = entry.get("summary") or entry.get("action") or "Configuration change"
        summary = f"{base}" + (f" — “{rationale}”" if rationale else "")
        jur = entry.get("jurisdiction")
        items.append({
            "ts": entry.get("ts"), "kind": "Rules", "actor": entry.get("author") or "",
            "summary": summary, "req_id": None, "candidate": None,
            "region": regionmap.region_for_jurisdiction(jur) if jur else None,
            "source": "rules",
        })

    # 3) Requisition / app events.
    pref_texts = _pref_texts()
    for ev in events.load_events():
        chip = _chip_for_event(ev.get("kind", ""))
        if chip == "Views" and not include_views:
            continue
        req_id = ev.get("req_id")
        summary = ev.get("summary") or ev.get("kind") or ""
        if chip == "Preference":
            summary = _with_pref_text(ev, summary, pref_texts)
        items.append({
            "ts": ev.get("ts"), "kind": chip, "actor": ev.get("actor") or "",
            "summary": summary, "req_id": req_id,
            "candidate": ev.get("candidate_id"),
            "region": _region_of_req(reqs, req_id), "source": "event",
        })

    # 4) Screening-run starts. The version stamps ride in the summary (P51): now
    # that the checklist AND the scoring model can change, a run row must say
    # which ones produced its numbers.
    for run in runs:
        stamps = " · ".join(
            s for s in (f"checklist {run['checklist_version']}" if run.get("checklist_version") else None,
                        f"scoring {run['scoring_model']}" if run.get("scoring_model") else None)
            if s)
        items.append({
            "ts": run.get("when"), "kind": "Data", "actor": "",
            "summary": (f"Screening run · {run.get('role')} · {run.get('meta')}"
                        + (f" · {stamps}" if stamps else "")),
            "req_id": run.get("req_id"), "candidate": None,
            "region": regionmap.region_for_jurisdiction(run.get("jurisdiction") or ""),
            "source": "run",
        })

    if kind:
        items = [i for i in items if i["kind"] == kind]
    if q:
        needle = q.lower()
        items = [i for i in items
                 if needle in " ".join(str(i.get(k, "")) for k in
                                       ("summary", "actor", "candidate", "req_id")).lower()]
    items.sort(key=lambda i: i.get("ts") or "", reverse=True)
    return items


@router.get("/feed")
def feed(request: Request, ident: tuple[str, str] = Depends(audit_identity),
         q: str = "", kind: str | None = None, region: str | None = None) -> dict:
    accessible = _enforce_region(ident[0], region)
    rows = [i for i in build_feed(q, kind, show_verdict=_shows_verdict())
            if _visible(i["region"], region, accessible)]
    _log_view(ident[0], ident[1], "feed", {"q": q, "kind": kind, "region": region})
    return {"entries": rows,
            "kinds": ["Decision", "Preference", "Rules", "Close", "Data", "Views"]}


# --- GET /api/audit/candidates -----------------------------------------------------


@router.get("/candidates")
def candidates(request: Request, ident: tuple[str, str] = Depends(audit_identity),
               q: str = Query(""), region: str | None = None) -> dict:
    """Resolve a name across every requisition's private map → the matching
    (requisition, candidate) pairs the timeline can open. Empty query → no matches
    (the whole roster isn't dumped)."""
    accessible = _enforce_region(ident[0], region)
    matches: list[dict] = []
    needle = q.strip().lower()
    if needle:
        cfg = service.config()
        for req in service.requisitions(cfg):
            item_region = regionmap.region_for_jurisdiction(req.job_location)
            if not _visible(item_region, region, accessible):
                continue
            private_map = service._private_map(req.req_id)
            for cid in private_map:
                name = service.candidate_name(req.req_id, cid, private_map)
                if needle in name.lower():
                    matches.append({"req_id": req.req_id, "req_title": service.display_title(req),
                                    "candidate_id": cid, "name": name,
                                    "region": item_region})
    _log_view(ident[0], ident[1], "candidates", {"q": q, "region": region})
    return {"matches": matches}


# --- GET /api/audit/candidates/{req}/{cand}/timeline -------------------------------


def build_timeline(req, candidate_id: str) -> dict[str, Any] | None:
    """Screened → HM take → Decision → Handed-off, plus the action targets. Returns
    None when the candidate isn't in this requisition's run."""
    meta = service.read_run_meta(req.req_id)
    rec = next((r for r in service.read_candidate_records(req.req_id)
                if r["score"]["candidate_id"] == candidate_id), None)
    if not meta or rec is None:
        return None
    run_id = str(meta.get("run_id", ""))
    s = rec["score"]
    eligible = bool(s["layer1"].get("eligible", True))
    steps: list[dict] = [{
        "step": "screened",
        "ts": meta.get("started_utc"),
        "score": round(s["final_score"]),
        "band": band_label(s["fit_band"], eligible),
        "gates": {"total": len(rec["findings"]["findings"]), "failed": _failed_gates(s["layer1"])},
        "run_id": run_id,
        "checklist_version": meta.get("versions", {}).get("rubric"),
        "pack": meta.get("policy_pack"),
        # P51: which scoring model produced this number (now that it can change)
        "scoring_model": meta.get("versions", {}).get("scoring_model"),
    }]

    take = hm_takes.takes_by_candidate(req.req_id, run_id).get(candidate_id)
    if take:
        steps.append({"step": "hm_take", "ts": take.get("ts"),
                      "who": take.get("hm_name", ""), "take": take.get("take")})

    decision = feedback.latest_decision_for(feedback.load_decisions(), run_id, candidate_id)
    handed_off = False
    if decision is not None and not service._is_cleared(decision):
        action = service.action_from_decision(decision)
        steps.append({"step": "decision", "ts": decision.ts,
                      "who": decision.reviewer or "",
                      "action": service.ACTION_LABEL[action],
                      "reason": decision.reason})  # VERBATIM
        handed_off = action == "forward"
    if handed_off:
        steps.append({"step": "handed_off",
                      "note": "Handed off — hire status lives in iCIMS."})

    return {
        "req_id": req.req_id,
        "candidate_id": candidate_id,
        "name": service.candidate_name(req.req_id, candidate_id),
        "tz": service.req_timezone(req),
        "steps": steps,
        "targets": {
            "audit_pack_url": f"/api/roles/{req.req_id}/audit",
            "checklist_version": meta.get("versions", {}).get("rubric"),
            "scoring_model": meta.get("versions", {}).get("scoring_model"),
            "jd_ref": req.jd_ref,
            "run_id": run_id,
        },
    }


@router.get("/candidates/{req_id}/{candidate_id}/timeline")
def candidate_timeline(req_id: str, candidate_id: str, request: Request,
                       ident: tuple[str, str] = Depends(audit_identity)) -> dict:
    from resume_screener.api.app import _req_authorized

    from resume_screener.api import verdict
    from resume_screener.api.app import _request_caps

    req = _req_authorized(req_id, ident)  # 404 + region 403 (P38)
    timeline = verdict.timeline_for(build_timeline(req, candidate_id), _request_caps())
    if timeline is None:
        raise HTTPException(404, "No screening record for this candidate.")
    _log_view(ident[0], ident[1], "candidate_timeline",
              {"req_id": req_id, "candidate_id": candidate_id})
    return timeline


# --- GET /api/audit/requisitions ---------------------------------------------------


def _req_card(req, events_by_req: dict[str, list[dict]]) -> dict:
    meta = service.req_meta(req)
    life = service.req_lifecycle(req)
    trail = sorted(events_by_req.get(req.req_id, []), key=lambda e: e.get("ts") or "",
                   reverse=True)[:8]
    return {
        "req_id": req.req_id,
        "title": service.display_title(req),
        "req_code": meta.get("icims_req_id"),
        "jurisdiction": req.job_location,
        "region": regionmap.region_for_jurisdiction(req.job_location),
        "status": life["status"],
        "created_by": life["created_by"],
        "events": [{"ts": e.get("ts"), "kind": e.get("kind"), "actor": e.get("actor"),
                    "summary": e.get("summary")} for e in trail],
    }


@router.get("/requisitions")
def requisitions_view(request: Request, ident: tuple[str, str] = Depends(audit_identity),
                      req: str | None = None, region: str | None = None) -> dict:
    """Requisition cards with event mini-timelines. `req=` narrows to one (the
    deep-link target from History's "View audit")."""
    accessible = _enforce_region(ident[0], region)
    cfg = service.config()
    all_events = events.load_events()
    events_by_req: dict[str, list[dict]] = {}
    for ev in all_events:
        rid = ev.get("req_id")
        if rid:
            events_by_req.setdefault(rid, []).append(ev)

    cards = []
    for requisition in service.requisitions(cfg):
        if req and requisition.req_id != req:
            continue
        item_region = regionmap.region_for_jurisdiction(requisition.job_location)
        if not _visible(item_region, region, accessible):
            continue
        cards.append(_req_card(requisition, events_by_req))
    cards.sort(key=lambda c: c["title"].lower())
    _log_view(ident[0], ident[1], "requisitions", {"req": req, "region": region})
    return {"requisitions": cards}


# --- GET /api/audit/rules ----------------------------------------------------------


@router.get("/rules")
def rules_log(request: Request, ident: tuple[str, str] = Depends(audit_identity),
              region: str | None = None) -> dict:
    """Config-governance history: registry/pack publishes + review sign-offs (with
    rationale quotes) merged with checklist publishes and preference approve/decline
    events. Newest first."""
    accessible = _enforce_region(ident[0], region)
    reqs, _runs, _run_by_id = _context()
    entries: list[dict] = []

    for e in governance.load_audit():
        jur = e.get("jurisdiction")
        entries.append({
            "ts": e.get("ts"), "kind": "config", "action": e.get("action"),
            "actor": e.get("author") or "", "summary": e.get("summary") or e.get("action"),
            "rationale": e.get("rationale"), "version": e.get("version"),
            # P51: the second leader who approved the change proposal (None =
            # a direct publish / a non-publish entry)
            "approved_by": e.get("approved_by"),
            "region": regionmap.region_for_jurisdiction(jur) if jur else None,
        })

    pref_texts = _pref_texts()
    for ev in events.load_events(kinds=["checklist_publish", "checklist_approval_requested",
                                        "checklist_approved", "pref_approve", "pref_decline"]):
        rid = ev.get("req_id")
        summary = ev.get("summary")
        if str(ev.get("kind", "")).startswith("pref_"):
            summary = _with_pref_text(ev, summary or "", pref_texts)
        entries.append({
            "ts": ev.get("ts"), "kind": ev.get("kind"), "action": ev.get("kind"),
            "actor": ev.get("actor") or "", "summary": summary,
            "rationale": (ev.get("data") or {}).get("reason"), "version": None,
            "region": _region_of_req(reqs, rid),
        })

    entries = [e for e in entries if _visible(e["region"], region, accessible)]
    entries.sort(key=lambda e: e.get("ts") or "", reverse=True)
    _log_view(ident[0], ident[1], "rules", {"region": region})
    return {"entries": entries}


# --- GET /api/audit/retention + holds ----------------------------------------------


def _keep_str(days: int | None) -> str:
    if not days:
        return "—"
    if days % 365 == 0:
        yrs = days // 365
        return f"{yrs} yr" if yrs == 1 else f"{yrs} yrs"
    return f"{days} days"


def retention_summaries(cfg=None) -> list[str]:
    """Per-jurisdiction plain-language duty strings, e.g. 'IN — DPDP Act 2023:
    audit records kept ≥ 3 yrs · candidate PII purged 365 days after close'."""
    from resume_screener.policy import PolicyError, resolve_policy

    cfg = cfg or service.config()
    policies_dir = Path(cfg.policy.policies_dir)
    codes = sorted(p.stem for p in policies_dir.glob("*.yaml") if p.stem != "base")
    out: list[str] = []
    for code in codes:
        try:
            eff = resolve_policy(code, policies_dir)
        except PolicyError:
            continue
        law = service.jurisdiction_law(code) or "policy"
        out.append(
            f"{code} — {law}: audit records kept ≥ {_keep_str(eff.keep_at_least_days)} · "
            f"candidate PII purged {eff.purge_by_days or '—'} days after close"
        )
    return out


@router.get("/retention")
def retention_view(request: Request, ident: tuple[str, str] = Depends(audit_identity),
                   region: str | None = None) -> dict:
    accessible = _enforce_region(ident[0], region)
    rows = [r for r in retention.purge_queue()
            if _visible(regionmap.region_for_jurisdiction(r["jurisdiction"]), region, accessible)]
    _log_view(ident[0], ident[1], "retention", {"region": region})
    return {"queue": rows, "summaries": retention_summaries()}


@router.post("/retention/{req_id}/hold")
def place_hold(req_id: str, request: Request, payload: dict = Body(default={}),
               ident: tuple[str, str] = Depends(audit_identity)) -> dict:
    from resume_screener.api.app import _req_authorized

    _req_authorized(req_id, ident)  # region 403 (P38)
    try:
        entry = retention.apply_hold(req_id, by=ident[0], reason=str(payload.get("reason", "")),
                                     actor_role=ident[1])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _log_view(ident[0], ident[1], "retention_hold", {"req_id": req_id})
    return {"ok": True, "hold": entry}


@router.post("/retention/{req_id}/release")
def lift_hold(req_id: str, request: Request,
              ident: tuple[str, str] = Depends(audit_identity)) -> dict:
    from resume_screener.api.app import _req_authorized

    _req_authorized(req_id, ident)  # region 403 (P38)
    try:
        retention.release_hold(req_id, by=ident[0], actor_role=ident[1])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _log_view(ident[0], ident[1], "retention_release", {"req_id": req_id})
    return {"ok": True}


# --- GET /api/audit/reports --------------------------------------------------------

_REPORTS = [
    {"key": "throughput_by_region", "label": "Screening throughput by region", "status": "ready"},
    {"key": "decision_mix_by_band", "label": "Decision mix by fit band", "status": "ready",
     "verdict": True},
    {"key": "time_to_shortlist", "label": "Time to shortlist", "status": "ready"},
    {"key": "adverse_impact", "label": "Adverse-impact monitor", "status": "Phase 2"},
]


@router.get("/reports")
def reports_catalog(request: Request, ident: tuple[str, str] = Depends(audit_identity),
                    region: str | None = None) -> dict:
    _enforce_region(ident[0], region)
    _log_view(ident[0], ident[1], "reports", {"region": region})
    shows = _shows_verdict()
    return {"reports": [{k: v for k, v in r.items() if k != "verdict"} for r in _REPORTS
                        if shows or not r.get("verdict")]}


def _csv(rows: list[list[Any]]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def report_rows(key: str, region: str | None,
                accessible: set[str] | None) -> list[list[Any]]:
    reqs, runs, run_by_id = _context()

    if key == "throughput_by_region":
        tally: dict[str, int] = {}
        for run in runs:
            r = regionmap.region_for_jurisdiction(run.get("jurisdiction") or "")
            if not _visible(r, region, accessible):
                continue
            tally[r] = tally.get(r, 0) + 1
        return [["region", "runs"], *[[r, n] for r, n in sorted(tally.items())]]

    if key == "decision_mix_by_band":
        # band × action tally across all requisitions' latest decisions.
        actions = ("forward", "discuss", "not")
        mix: dict[str, dict[str, int]] = {}
        for req in reqs.values():
            r = regionmap.region_for_jurisdiction(req.job_location)
            if not _visible(r, region, accessible):
                continue
            bands = {rec["score"]["candidate_id"]: rec["score"]["fit_band"]
                     for rec in service.read_candidate_records(req.req_id)}
            for cid, dec in service.role_decisions(req.req_id).items():
                band = bands.get(cid, "Unknown")
                mix.setdefault(band, {a: 0 for a in actions})
                if dec["action"] in actions:
                    mix[band][dec["action"]] += 1
        return [["band", "move_forward", "discuss", "not_a_fit"],
                *[[b, m["forward"], m["discuss"], m["not"]] for b, m in sorted(mix.items())]]

    if key == "time_to_shortlist":
        rows: list[list[Any]] = [["req_id", "title", "run_started", "last_decision", "days"]]
        for req in reqs.values():
            r = regionmap.region_for_jurisdiction(req.job_location)
            if not _visible(r, region, accessible):
                continue
            meta = service.read_run_meta(req.req_id)
            if not meta:
                continue
            start = service._parse_ts(meta.get("started_utc"))
            decisions = service.role_decisions(req.req_id)
            stamps = [service._parse_ts(d["ts"]) for d in decisions.values() if d.get("ts")]
            stamps = [s for s in stamps if s is not None]
            if start is None or not stamps:
                continue
            last = max(stamps)
            rows.append([req.req_id, service.display_title(req),
                         start.date().isoformat(), last.date().isoformat(),
                         (last - start).days])
        return rows

    raise HTTPException(404, f"No report '{key}'.")


@router.get("/reports/{key}.csv")
def report_csv(key: str, request: Request, ident: tuple[str, str] = Depends(audit_identity),
               region: str | None = None,
               _rl: None = Depends(ratelimit.limited("export"))) -> Response:
    if key == "adverse_impact":
        raise HTTPException(400, "The adverse-impact monitor is a Phase 2 report.")
    if any(r["key"] == key and r.get("verdict") for r in _REPORTS) and not _shows_verdict():
        from resume_screener.api import verdict

        raise HTTPException(403, verdict.DENIED_MESSAGE)
    accessible = _enforce_region(ident[0], region)
    rows = report_rows(key, region, accessible)
    _log_view(ident[0], ident[1], f"report:{key}", {"region": region})
    return Response(content=_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{key}.csv"'})
