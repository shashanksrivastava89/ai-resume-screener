"""Reports (P11, §5.7): everything a run leaves behind for humans and audits.

Files written into --out:
  shortlist.xlsx / shortlist.csv   the ranked table HR actually opens
  candidates/<id>.json             the FULL record per candidate (score, findings
                                   with quotes, features, enforcement, critic) —
                                   what `explain` and the UI read
  report.html                      the same, readable in a browser, no tooling
  run_meta.json                    the audit stamp: jurisdiction + obligations,
                                   every version, counts, cost estimate, errors

Ranking rule (`scoring.rank_key`, shared with the Review list): everyone who meets
every hard gate first, then band (Strong > Possible > Not a fit), then final score.
A gate-failed candidate with a high raw score therefore sits UNDER every eligible
candidate — but still in the table with their reasons. Never hidden, never first
(§6.3 + honest UX).

Security note: resume quotes are untrusted text — everything interpolated into
HTML goes through html.escape (a resume containing <script> must render as
text, not run).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from pydantic import BaseModel

from resume_screener.critic import apply_critic
from resume_screener.features import FeatureSet
from resume_screener.findings import FindingsRecord
from resume_screener.location_context import LocationContext
from resume_screener.models import Rubric, ScoreResult
from resume_screener.scoring import band_label, rank_key

# Approximate list prices for the cost ESTIMATE in run_meta (July 2026; update
# for your deployments/region — this is a sanity figure, not billing).
_USD_PER_M_CHAT_INPUT = 2.00  # gpt-4.1 class
_USD_PER_M_CHAT_OUTPUT = 8.00
_USD_PER_M_EMBEDDING = 0.13  # text-embedding-3-large
_USD_PER_DI_PAGE = 0.01  # prebuilt-layout


class CandidateBundle(BaseModel):
    """Everything the run pipeline hands the report writer for one candidate."""

    score: ScoreResult
    findings: FindingsRecord
    features: FeatureSet | None = None
    layer2_enforcement: list[str] = []
    # NON-SCORING location context (2026-08-26): how far the résumé's stated place is
    # from the requisition's. Sits OUTSIDE `score` on purpose — it is reviewer context
    # frozen into the artifact, never an input to a band, a weight or an adjustment.
    location: LocationContext | None = None
    extraction_confidence: float | None = None
    extractor: str = ""  # "local" | "document-intelligence" | "python-docx"
    route: str = ""  # why it was read there (ingest.ExtractionResult.route)
    extraction_stats: dict = {}  # localextract's per-file counts
    pages: int = 0  # Document Intelligence pages billed by this run (0 = local or cached)


def estimate_cost_usd(meter: dict, di_pages: int) -> float:
    return round(
        meter.get("chat_prompt_tokens", 0) / 1e6 * _USD_PER_M_CHAT_INPUT
        + meter.get("chat_completion_tokens", 0) / 1e6 * _USD_PER_M_CHAT_OUTPUT
        + meter.get("embedding_tokens", 0) / 1e6 * _USD_PER_M_EMBEDDING
        + di_pages * _USD_PER_DI_PAGE,
        4,
    )


def top_gaps(bundle: CandidateBundle, rubric: Rubric, n: int = 3) -> list[str]:
    """The highest-weight requirements this candidate did NOT fully meet —
    the 'what to probe in the interview' list."""
    weight_of = {r.id: r.weight for r in rubric.normalized().requirements}
    gaps = [
        (weight_of.get(f.requirement_id, 0), f)
        for f in bundle.findings.findings
        if f.match != "full" and f.requirement_id in weight_of
    ]
    gaps.sort(key=lambda pair: pair[0], reverse=True)
    return [f"{f.requirement_text} ({f.match})" for _, f in gaps[:n]]


def rank_bundles(bundles: list[CandidateBundle]) -> list[CandidateBundle]:
    """Same order the Review list shows — one rule, in `scoring.rank_key`."""
    return sorted(
        bundles,
        key=lambda b: rank_key(b.score.fit_band, b.score.final_score,
                               b.score.candidate_id, eligible=b.score.layer1.eligible),
        reverse=True,
    )


def _rows(bundles: list[CandidateBundle], rubric: Rubric) -> list[dict]:
    """The shortlist table — exact columns per the build spec."""
    rows = []
    for rank, bundle in enumerate(rank_bundles(bundles), start=1):
        score = bundle.score
        # Split the effective delta into its two sources (P40): pref_total is the
        # HR-preference nudge; adj_total stays Layer-2-only, so a no-prefs run's
        # adj_total is unchanged (pref_total is 0 and the arithmetic collapses).
        pref_total = round(sum(p.points for p in score.preference_adjustments), 2)
        surviving_net = round(score.final_score - score.layer1.base_score - pref_total, 2)
        critic_flags = []
        if score.critic:
            critic_flags = [
                f"{sid}:{verdict}"
                for sid, verdict in score.critic.verdict_per_adjustment.items()
                if verdict != "pass"
            ] + score.critic.overall_flags
        rows.append(
            {
                "rank": rank,
                "candidate_id": score.candidate_id,
                "band": band_label(score.fit_band, score.layer1.eligible),
                "final": score.final_score,
                "base": score.layer1.base_score,
                "adj_total": surviving_net,
                "pref_total": pref_total,
                "confidence": score.confidence,
                "must_haves_missing": ", ".join(score.layer1.must_haves_missing),
                "top_gaps": " | ".join(top_gaps(bundle, rubric)),
                "critic_flags": " | ".join(critic_flags),
            }
        )
    return rows


def _candidate_json(bundle: CandidateBundle) -> dict:
    """The full per-candidate record. Includes the suspended queue explicitly so
    the UI doesn't have to re-derive it."""
    suspended = []
    if bundle.score.layer2 and bundle.score.critic:
        _, suspended_adjustments = apply_critic(bundle.score.layer2, bundle.score.critic)
        suspended = [a.model_dump() for a in suspended_adjustments]
    return {
        "score": bundle.score.model_dump(),
        "findings": bundle.findings.model_dump(),
        "features": bundle.features.model_dump() if bundle.features else None,
        "layer2_enforcement": bundle.layer2_enforcement,
        "suspended_adjustments": suspended,
        # Frozen with the run so Review reads a number instead of re-parsing every
        # résumé off the share on each page load.
        "location": bundle.location.model_dump() if bundle.location else None,
        "extraction": {
            "confidence": bundle.extraction_confidence,
            "extractor": bundle.extractor,
            "route": bundle.route,
            "stats": bundle.extraction_stats,
            "pages": bundle.pages,
        },
    }


def _html_report(bundles: list[CandidateBundle], rubric: Rubric, run_meta: dict) -> str:
    """Single-file, dependency-free HTML. Everything escaped — quotes are
    untrusted resume text."""
    e = html.escape
    marks = {"full": "✓", "partial": "~", "none": "✗"}
    verdict_badge = {"pass": "✓ pass", "flag": "⚑ flag", "suspend": "⛔ suspend"}

    head = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Resume Screener — {e(rubric.role_title)}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:2em;max-width:70em}}
 table{{border-collapse:collapse;width:100%;margin:1em 0}}
 th,td{{border:1px solid #ccc;padding:.4em .6em;text-align:left;vertical-align:top}}
 th{{background:#f0f0f0}} .band-Strong{{background:#e6f4e6}} .band-Possible{{background:#fff8e1}}
 .band-Weak{{background:#fbeaea}} .band-Gate{{background:#fbeaea}} .quote{{color:#555;font-style:italic}}
 .suspend{{background:#fbeaea}} .flag{{background:#fff8e1}}
 details{{margin:.6em 0}} summary{{cursor:pointer;font-weight:600}}
 .badge{{display:inline-block;background:#eef;border:1px solid #99c;border-radius:4px;padding:.1em .5em;margin-right:.4em}}
</style></head><body>
<h1>Shortlist — {e(rubric.role_title)}</h1>
<p><span class="badge">Jurisdiction: {e(str(run_meta.get('jurisdiction')))}</span>
<span class="badge">Policy pack: {e(str(run_meta.get('policy_pack')))}</span>
<span class="badge">Rubric v{rubric.version}</span>
<span class="badge">Run: {e(str(run_meta.get('run_id')))}</span></p>
<p><strong>Decision-support only:</strong> nothing here rejects anyone; a human decides.
Internal evaluation only until the HR + Legal review completes.</p>
<details><summary>Obligations checklist ({len(run_meta.get('obligations', []))}) — displayed, not enforced</summary>
<ul>{''.join(f'<li>{e(o)}</li>' for o in run_meta.get('obligations', []))}</ul></details>"""

    table_rows = "".join(
        # band CSS class: first word only ("Weak match" -> band-Weak, "Gate not met" -> band-Gate)
        f"<tr class='band-{row['band'].split()[0]}'>"
        f"<td>{row['rank']}</td><td><a href='#c-{e(row['candidate_id'])}'>{e(row['candidate_id'])}</a></td>"
        f"<td>{e(row['band'])}</td><td>{row['final']:.1f}</td><td>{row['base']:.1f}</td>"
        f"<td>{row['adj_total']:+.1f}</td><td>{e(row['confidence'])}</td>"
        f"<td>{e(row['must_haves_missing'])}</td><td>{e(row['top_gaps'])}</td>"
        f"<td>{e(row['critic_flags'])}</td></tr>"
        for row in _rows(bundles, rubric)
    )
    summary_table = (
        "<table><tr><th>rank</th><th>candidate</th><th>band</th><th>final</th><th>base</th>"
        "<th>adj</th><th>confidence</th><th>must-haves missing</th><th>top gaps</th>"
        f"<th>critic flags</th></tr>{table_rows}</table>"
    )

    sections = []
    for bundle in rank_bundles(bundles):
        score = bundle.score
        suspended_ids = set()
        if score.layer2 and score.critic:
            _, suspended_adjustments = apply_critic(score.layer2, score.critic)
            suspended_ids = {a.signal_id for a in suspended_adjustments}

        findings_rows = "".join(
            f"<tr><td>{marks[f.match]}</td><td>{e(f.requirement_text)}</td>"
            f"<td class='quote'>{e(f.evidence or '')}</td><td>{e(f.note or '')}</td></tr>"
            for f in bundle.findings.findings
        )
        adjustments_rows = ""
        if score.layer2:
            for a in score.layer2.adjustments:
                verdict = (score.critic.verdict_per_adjustment.get(a.signal_id, "pass")
                           if score.critic else "pass")
                css = " class='suspend'" if a.signal_id in suspended_ids else (
                    " class='flag'" if verdict == "flag" else "")
                adjustments_rows += (
                    f"<tr{css}><td>{a.points:+.1f}</td><td>{e(a.signal_id)}</td>"
                    f"<td>{e(verdict_badge[verdict])}</td>"
                    f"<td class='quote'>{e(a.evidence)}</td><td>{e(a.rationale)}</td></tr>"
                )
        critic_notes = ""
        if score.critic and (score.critic.notes or score.critic.overall_flags):
            items = "".join(f"<li>{e(n)}</li>" for n in score.critic.notes + score.critic.overall_flags)
            critic_notes = f"<details><summary>Critic notes</summary><ul>{items}</ul></details>"

        sections.append(f"""
<h2 id="c-{e(score.candidate_id)}">Candidate {e(score.candidate_id)} — {e(band_label(score.fit_band, score.layer1.eligible))}
 ({score.final_score:.1f})</h2>
<p>{e(score.summary_rationale)}</p>
<p>Base {score.layer1.base_score:.1f} · eligible: {score.layer1.eligible} ·
 confidence: {e(score.confidence)} · extraction: {e(bundle.extractor)}
 {'' if bundle.extraction_confidence is None else f'({bundle.extraction_confidence:.2f})'}</p>
{'<p><strong>Reasons:</strong> ' + e('; '.join(score.layer1.reasons)) + '</p>' if score.layer1.reasons else ''}
<details><summary>Requirement findings ({len(bundle.findings.findings)})</summary>
<table><tr><th></th><th>requirement</th><th>evidence (verbatim)</th><th>note</th></tr>{findings_rows}</table></details>
{'<details open><summary>Signal adjustments</summary><table><tr><th>points</th><th>signal</th><th>critic</th><th>evidence</th><th>rationale</th></tr>' + adjustments_rows + '</table></details>' if adjustments_rows else '<p>No Layer-2 adjustments (gate-blocked or none proposed).</p>'}
{'<p><strong>Enforcement:</strong> ' + e('; '.join(bundle.layer2_enforcement)) + '</p>' if bundle.layer2_enforcement else ''}
{critic_notes}
{'<p><strong>Narrative:</strong> ' + e(score.layer2.narrative) + '</p>' if score.layer2 and score.layer2.narrative else ''}""")

    return head + summary_table + "".join(sections) + "</body></html>"


CANDIDATES_INDEX = "candidates_index.json"


def candidates_index_path(out_dir) -> "Path":
    from pathlib import Path as _P

    return _P(out_dir) / CANDIDATES_INDEX


def write_reports(
    out_dir: str | Path,
    bundles: list[CandidateBundle],
    rubric: Rubric,
    run_meta: dict,
) -> dict[str, Path]:
    """Write everything. Returns the paths (the CLI prints them)."""
    import pandas as pd

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    frame = pd.DataFrame(
        _rows(bundles, rubric),
        columns=["rank", "candidate_id", "band", "final", "base", "adj_total",
                 "pref_total", "confidence", "must_haves_missing", "top_gaps", "critic_flags"],
    )
    paths["xlsx"] = out / "shortlist.xlsx"
    frame.to_excel(paths["xlsx"], index=False)
    paths["csv"] = out / "shortlist.csv"
    frame.to_csv(paths["csv"], index=False)

    candidates_dir = out / "candidates"
    candidates_dir.mkdir(exist_ok=True)
    for bundle in bundles:
        path = candidates_dir / f"{bundle.score.candidate_id}.json"
        path.write_text(json.dumps(_candidate_json(bundle), indent=2, ensure_ascii=False),
                        encoding="utf-8")
    paths["candidates_dir"] = candidates_dir

    # The four fields a dashboard card needs from a run, in ONE file (2026-09-10).
    # Building a card otherwise means opening every candidate's record: 350 reads per
    # requisition, 175,000 across a 500-requisition deployment, every time the cards
    # have to be rebuilt. The full records stay exactly as they are — Review and the
    # drawer need all of it — this is a summary beside them, not a replacement.
    paths["candidates_index"] = candidates_index_path(out)
    paths["candidates_index"].write_text(
        json.dumps({"schema": 1, "candidates": [
            {"candidate_id": b.score.candidate_id, "fit_band": b.score.fit_band,
             "final_score": b.score.final_score,
             "eligible": bool(b.score.layer1.eligible)}
            for b in bundles
        ]}), encoding="utf-8")

    paths["html"] = out / "report.html"
    paths["html"].write_text(_html_report(bundles, rubric, run_meta), encoding="utf-8")

    paths["run_meta"] = out / "run_meta.json"
    paths["run_meta"].write_text(json.dumps(run_meta, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    return paths
