// "How it works" — the read-only story tab (v3 design): the live composition
// chart on the synthetic sample, the six-step résumé-to-decision sequence with
// links into the editable cards, and the "why it's built this way" read.
import { useState } from "react";
import type { ScoringDoc } from "../../types";
import ScoreChart, { type ScoreParts } from "../ScoreChart";
import { fmtN } from "./model";

const affect = (bg: string, fg: string): React.CSSProperties => ({
  display: "inline-flex", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.03em",
  textTransform: "uppercase", padding: "2px 8px", borderRadius: 6, background: bg, color: fg,
});

export function whyRows(doc: ScoringDoc) {
  return [
    { n: "1", head: "The checklist does most of the work.", body: `Each line has a weight; clear evidence pays the full weight, some evidence pays ${fmtN(doc.coverage.match_value.partial)}×, none pays ${fmtN(doc.coverage.match_value.none)}. Added up, that's the base — out of 100, re-checkable months later, line by line.` },
    { n: "2", head: "Judged signals nudge, never decide.", body: "Evidence-backed extras — quota attainment, regulated-environment exposure — move a few points at most; every one has a hard cap." },
    { n: "3", head: "The fairness critic reads every reason.", body: "Any adjustment leaning on a never-scored topic — gaps, age, salary, prestige… — is rejected by code. Still visible, worth 0." },
    { n: "4", head: "People decide.", body: `Nobody is auto-rejected. Personal preferences nudge ±${fmtN(doc.preferences.per_preference_cap_points)} at most, and every decision needs a human and a written reason.` },
  ];
}

export const WHY_FOOT =
  "The split is roughly 80/20 deterministic-to-judged on purpose: if a candidate — or a court — asks “why 71?”, four-fifths of the answer is arithmetic anyone can re-run. And none of this lives in code: every number on this page is policy — versioned, and approved by the people accountable for it.";

export function WhyRead({ doc, open, onToggle, compact = false }: {
  doc: ScoringDoc; open: boolean; onToggle: () => void; compact?: boolean;
}) {
  return (
    <div style={{ background: "var(--brand-green-tint)", border: "1px solid var(--brand-green)", borderRadius: 14, padding: "13px 18px", marginBottom: compact ? 12 : 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--brand-green-deep)" }}>
          Why it’s built this way — the 60-second read
        </span>
        <button onClick={onToggle} style={{ border: 0, background: "none", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)", whiteSpace: "nowrap" }}>
          {open ? "Hide" : "Read"}
        </button>
      </div>
      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
          {whyRows(doc).map((w) => (
            <div key={w.n} style={{ display: "flex", gap: 9, fontSize: 12.5, lineHeight: 1.55 }}>
              <span style={{ fontWeight: 700, color: "var(--brand-green-deep)", flex: "0 0 auto" }}>{w.n}</span>
              <span><strong>{w.head}</strong> {w.body}</span>
            </div>
          ))}
          <div style={{ fontSize: 12, color: "var(--ink-soft)", lineHeight: 1.6, borderTop: "1px solid rgba(0,166,81,0.25)", paddingTop: 8 }}>
            {WHY_FOOT}
          </div>
        </div>
      )}
    </div>
  );
}

export default function HowTab({ doc, publishedParts, goSection }: {
  doc: ScoringDoc;
  /** The synthetic sample scored under the PUBLISHED model (the real engine). */
  publishedParts: ScoreParts | null;
  goSection: (tab: string, card: string | null) => void;
}) {
  const [detail, setDetail] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);

  const linkTitle = (color: string): React.CSSProperties => ({
    fontSize: 13, fontWeight: 700, cursor: "pointer", color,
    textDecoration: "underline dotted", textUnderlineOffset: 3,
  });
  const plainTitle: React.CSSProperties = { fontSize: 13, fontWeight: 700 };

  const steps = [
    { n: "1", title: "Identity is stripped off first", onClick: undefined as (() => void) | undefined, titleStyle: plainTitle, chip: "System code — automatic", chipStyle: affect("var(--neutral-150)", "#5A5A5A"), body: "The candidate’s name, age, address, photos and contact details are removed from the résumé before anything scores it. Nothing downstream ever sees who they are." },
    { n: "2", title: "Checklist base (from the JD) — most of the score", onClick: () => goSection("score", "credits"), titleStyle: linkTitle("var(--brand-green-deep)"), chip: "Pure math — no AI", chipStyle: affect("var(--brand-green-tint)", "var(--brand-green-deep)"), body: "Every requisition has a requirement checklist its recruiter publishes (you’ll find it in the requisition’s workspace). Each line pays weight × evidence credit; summed, that’s the base, out of 100 — recomputable by hand, months later." },
    { n: "3", title: "AI signals — the AI’s capped nudges", onClick: () => goSection("score", "signals"), titleStyle: linkTitle("#16729E"), chip: "AI proposes — quoted evidence", chipStyle: affect("var(--accent-blue-tint)", "#16729E"), body: "An AI model reads the anonymized résumé and proposes small adjustments — “quota attainment +4” — each with a verbatim quote and a hard cap set on The score tab." },
    { n: "4", title: "Bias critic — checks the AI, not the person", onClick: () => goSection("critic", null), titleStyle: linkTitle("#5A5A5A"), chip: "System check — automatic", chipStyle: affect("var(--neutral-150)", "#5A5A5A"), body: "Runs after the AI proposes, before anything counts. Exact-phrase code plus a second AI audit the first AI’s written reasoning; anything leaning on a banned topic (Bias critic tab) is rejected — counts 0, stays visible." },
    { n: "5", title: "Preferences, then the fence", onClick: () => goSection("score", "prefs"), titleStyle: linkTitle("var(--warn-ink)"), chip: `Bounded · ±${fmtN(doc.preferences.per_preference_cap_points)}`, chipStyle: affect("var(--surface-warn)", "var(--warn-ink)"), body: `Approved personal preferences nudge at most ±${fmtN(doc.preferences.per_preference_cap_points)}. The total is held between ${fmtN(doc.clamp.min)} and ${fmtN(doc.clamp.max)} and gets its label — Strong ≥ ${fmtN(doc.bands.strong_at)}, Possible ≥ ${fmtN(doc.bands.possible_at)}.` },
    { n: "6", title: "People decide — always", onClick: undefined, titleStyle: plainTitle, chip: "You — with a reason", chipStyle: affect("var(--brand-green-tint)", "var(--brand-green-deep)"), body: "The score ranks and explains; it never rejects anyone. Recruiters record every decision with a written reason, kept in audit." },
  ];

  return (
    <>
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "16px 20px 12px", marginBottom: 14 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>What a score is made of</div>
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginBottom: 8 }}>
          Live on a synthetic sample — the checklist base does most of the work; AI signals and preferences only nudge, and a fence holds the total in range.
        </div>
        {publishedParts && (
          <ScoreChart
            parts={publishedParts}
            bounds={{ strongAt: doc.bands.strong_at, possibleAt: doc.bands.possible_at, clampMin: doc.clamp.min, clampMax: doc.clamp.max }}
          />
        )}
      </div>

      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "18px 20px", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 700 }}>From résumé to decision — the sequence</div>
            <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 2 }}>
              Read-only. Every editable number lives in the other tabs.
            </div>
          </div>
          <button onClick={() => setDetail((d) => !d)} style={{ border: 0, background: "none", fontFamily: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer", color: "var(--brand-green-deep)", whiteSpace: "nowrap" }}>
            {detail ? "Hide the walkthrough" : "Read the walkthrough"}
          </button>
        </div>
        {steps.map((h) => (
          <div key={h.n} style={{ display: "flex", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--neutral-150)", alignItems: "flex-start" }}>
            <span style={{ flex: "0 0 26px", width: 26, height: 26, borderRadius: "50%", background: "var(--brand-green-tint)", color: "var(--brand-green-deep)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 12 }}>
              {h.n}
            </span>
            <div style={{ flex: "1 1 auto", minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span onClick={h.onClick} style={h.titleStyle}>{h.title}</span>
                <span style={h.chipStyle}>{h.chip}</span>
              </div>
              {detail && (
                <div style={{ fontSize: 12, color: "var(--ink-soft)", lineHeight: 1.55, marginTop: 3 }}>{h.body}</div>
              )}
            </div>
          </div>
        ))}
      </div>

      <WhyRead doc={doc} open={whyOpen} onToggle={() => setWhyOpen((o) => !o)} />
    </>
  );
}
