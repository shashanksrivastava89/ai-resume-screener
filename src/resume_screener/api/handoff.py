"""Ask-Claude handoff (P44): a redacted context bundle the recruiter/HM copies
into claude.ai today (the MCP connector is Phase 2 — the UI contract won't
change then). Assembly is SERVER-SIDE on purpose:

- redaction/blind rules live on the server, not the client;
- the bundle needs ALL candidates for one requisition, not the open drawer;
- the audit event must be atomic with producing the text (the route emits it).

EXCLUDED BY CONSTRUCTION — the bundle is built only from the review records
(bands, gate results, checklist texts, evidence quotes already drawn from the
REDACTED résumé). It never touches the private map's file paths or candidate
ids, so contact details, source filenames, emails and 16-hex ids can't leak.
In blind mode names collapse to "Candidate N" by rank.

OFF BY DEFAULT (2026-10-23): the copy still carries candidates' names and the AI's
read on them to Claude, outside the app's access model, and whether that is allowed
waits on InfoSec (POL-00000 §4.5.6–4.5.8). The `ask_claude_handoff` flag turns it on;
while it is off the route refuses and the button is hidden.
"""

from __future__ import annotations

from resume_screener.api import service
from resume_screener.api.review import _failed_gates, _rank_key
from resume_screener.models import Requisition
from resume_screener.scoring import band_label

# The event line the route appends — recorded so a copy-out is never silent.
COPIED_SUMMARY = "Ask Claude context copied · redacted bundle · {user} · logged automatically."

FLAG = "ask_claude_handoff"
DISABLED_MESSAGE = ("Ask Claude is turned off until InfoSec confirms that candidate data "
                    "may be pasted into Claude.")


def _ranked_records(req: Requisition, meta: dict | None) -> list[dict]:
    """The same attached-and-ranked records the Review page shows (a run whose
    scored résumés are all off the batch is treated as not-yet-screened)."""
    if not meta:
        return []
    cfg = service.config()
    attached = service.batch_candidate_ids(cfg, req)
    return sorted(
        (r for r in service.read_candidate_records(req.req_id)
         if r["score"]["candidate_id"] in attached),
        key=_rank_key, reverse=True,
    )


def _strengths(rec: dict, limit: int = 3) -> list[str]:
    return [f["requirement_text"] for f in rec["findings"]["findings"]
            if f["match"] == "full"][:limit]


def _watchouts(rec: dict, limit: int = 3) -> list[str]:
    out = []
    for f in rec["findings"]["findings"]:
        if f["match"] == "partial":
            out.append("Partly shown: " + f["requirement_text"])
        elif f["match"] == "none":
            out.append("Missing: " + f["requirement_text"])
    return out[:limit]


def _evidence(rec: dict, limit: int = 3) -> list[str]:
    """Verbatim evidence quotes — already substring-verified against the REDACTED
    profile at findings time, so quoting them can't reveal contact details."""
    quotes = [f["evidence"] for f in rec["findings"]["findings"] if f.get("evidence")]
    return quotes[:limit]


def build_bundle(req: Requisition, *, blind: bool = False, decisions: bool = True) -> str:
    """The redacted markdown bundle for one requisition's whole shortlist.
    `decisions=False` (a viewer outside the requisition's team, 2026-10-21) leaves out
    the decision tally — the team's decisions are theirs (api/decision_view.py)."""
    meta = service.read_run_meta(req.req_id)
    records = _ranked_records(req, meta)
    title = service.display_title(req)
    code = service.req_meta(req).get("icims_req_id") or "—"
    place = service.jurisdiction_display(meta.get("jurisdiction", req.job_location) if meta else req.job_location)
    law = service.jurisdiction_law(meta.get("jurisdiction", req.job_location) if meta else req.job_location)

    lines: list[str] = [f"# Screening context — {title}", ""]
    lines.append(f"**Requisition:** {code} · {place}" + (f" · {law}" if law else ""))

    rubric = service.rubric_for(req)
    if rubric:
        lines.append(f"**Checklist:** v{rubric.version} — {len(rubric.requirements)} requirements")
    if blind:
        lines.append("**Blind review:** names withheld — candidates are numbered by rank.")
    lines.append(f"**Screened:** {len(records)} candidates")
    lines.append("")

    # --- checklist ---
    if rubric and rubric.requirements:
        lines.append("## Checklist")
        for r in rubric.requirements:
            marker = "[Gate] " if r.kind == "hard_gate" else "[Must] " if r.kind == "must_have" else ""
            lines.append(f"- {marker}{r.text}")
        lines.append("")

    if not records:
        lines.append("_No candidates have been screened yet._")
        return "\n".join(lines)

    def name_for(rank: int, rec: dict) -> str:
        if blind:
            return f"Candidate {rank}"
        return service.candidate_name(req.req_id, rec["score"]["candidate_id"])

    # --- ranked band/gate/score table ---
    lines.append("## Candidates (ranked)")
    lines.append("| Rank | Candidate | Band | Gate | Score |")
    lines.append("|---|---|---|---|---|")
    for rank, rec in enumerate(records, start=1):
        s = rec["score"]
        eligible = bool(s["layer1"].get("eligible", True))
        gate = "Gate not met" if _failed_gates(s["layer1"]) else "Passed"
        lines.append(
            f"| {rank} | {name_for(rank, rec)} | {band_label(s['fit_band'], eligible)} "
            f"| {gate} | {round(s['final_score'])} |"
        )
    lines.append("")

    # --- per-candidate evidence ---
    lines.append("## Evidence")
    for rank, rec in enumerate(records, start=1):
        s = rec["score"]
        eligible = bool(s["layer1"].get("eligible", True))
        lines.append("")
        lines.append(f"### {rank}. {name_for(rank, rec)} — {band_label(s['fit_band'], eligible)} "
                     f"({round(s['final_score'])})")
        strengths = _strengths(rec)
        if strengths:
            lines.append("Strengths:")
            lines += [f"- {t}" for t in strengths]
        watchouts = _watchouts(rec)
        if watchouts:
            lines.append("Watch-outs:")
            lines += [f"- {t}" for t in watchouts]
        quotes = _evidence(rec)
        if quotes:
            lines.append("Evidence:")
            lines += [f"- “{q}”" for q in quotes]
    lines.append("")

    if not decisions:
        return "\n".join(lines)

    # --- decision counts (never the verbatim reasons here) ---
    decisions = service.role_decisions(req.req_id)
    tally = {"forward": 0, "discuss": 0, "not": 0}
    for d in decisions.values():
        if d["action"] in tally:
            tally[d["action"]] += 1
    awaiting = len(records) - sum(tally.values())
    lines.append("## Decisions so far")
    lines.append(f"Move forward: {tally['forward']} · Discuss: {tally['discuss']} · "
                 f"Not a fit: {tally['not']} · Awaiting: {max(0, awaiting)}")

    return "\n".join(lines)
