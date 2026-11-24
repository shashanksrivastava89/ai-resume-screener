// The "Batch quality" tab (2026-07-19): the BATCH-level read — deliberately its
// own tab, away from the candidate-score knobs, so nobody confuses the two.
// Governed like everything else: the three knobs stage into the same draft and
// go to a second leader as one proposal.
import type { ScoringDoc, ScoringKnob } from "../../types";
import BatchQualityChip from "../BatchQualityChip";
import { fmtN } from "./model";
import { KnobRow } from "./ScoreTab";

const BATCH_KNOB_UI: Record<string, { step: number; min: number; max: number; fmt: (v: number) => string }> = {
  "batch_quality.top_n": { step: 1, min: 1, max: 50, fmt: (v) => `top ${fmtN(v)}` },
  "batch_quality.strong_at": { step: 1, min: 0, max: 100, fmt: (v) => `≥ ${fmtN(v)}` },
  "batch_quality.okay_at": { step: 1, min: 0, max: 100, fmt: (v) => `≥ ${fmtN(v)}` },
};

export default function BatchTab({ doc, published, knobs, stage }: {
  doc: ScoringDoc;
  published: ScoringDoc;
  knobs: ScoringKnob[];
  stage: (mutate: (d: ScoringDoc) => void) => void;
}) {
  const byPath = new Map(knobs.map((k) => [k.path, k]));
  const bq = doc.batch_quality;
  const sample = (score: number) => ({
    score,
    band: (score >= bq.strong_at ? "strong" : score >= bq.okay_at ? "okay" : "weak") as
      "strong" | "okay" | "weak",
    label: score >= bq.strong_at ? "Strong batch" : score >= bq.okay_at ? "Okay batch" : "Weak batch",
    basis: `Average score of the top ${bq.top_n} gate-passing candidates`,
    top_n: bq.top_n,
    thresholds: { strong_at: bq.strong_at, okay_at: bq.okay_at },
  });

  return (
    <div>
      {/* what this number is — and, just as important, what it is NOT */}
      <div style={{ background: "var(--white)", border: "1px solid var(--border-subtle)", borderRadius: 14, padding: "16px 20px", marginBottom: 14 }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 6 }}>
          One number per requisition: “can I fill this role from this batch?”
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)", lineHeight: 1.6 }}>
          The batch score is the <strong style={{ color: "var(--ink)" }}>average final score of the
          top {fmtN(bq.top_n)} candidates who passed the hard gates</strong> (fewer pass → it averages
          those). Not a plain average of everyone: a big honest batch would be punished for its long
          tail, and a tiny mediocre one rewarded — hiring is decided by the best few résumés, so the
          read looks at the head of the batch. It refreshes with every screen and re-labels the moment
          these thresholds change.
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 12, fontSize: 12, color: "var(--ink-soft)" }}>
          <span style={{ fontWeight: 700, color: "var(--ink)" }}>Live preview:</span>
          <BatchQualityChip bq={sample(Math.max(bq.strong_at + 5, bq.strong_at))} />
          <BatchQualityChip bq={sample((bq.strong_at + bq.okay_at) / 2)} />
          <BatchQualityChip bq={sample(Math.max(0, bq.okay_at - 10))} />
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-muted)", marginTop: 10 }}>
          Batch-level only — no candidate's own score, band or ranking is affected by anything on
          this tab. It appears on the requisitions list and the Candidates tab after a screen.
        </div>
      </div>

      {/* the three governed knobs — same draft, same proposal, same second leader */}
      <div style={{ background: "var(--white)", border: "1.5px solid var(--border-subtle)", borderRadius: 14, padding: "16px 20px" }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 2 }}>Batch bands</div>
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginBottom: 8, lineHeight: 1.55 }}>
          Below “Okay batch” the label reads <strong style={{ color: "var(--ink)" }}>Weak batch</strong> —
          usually a signal to source more résumés rather than re-read these.
        </div>
        {Object.entries(BATCH_KNOB_UI).map(([path, ui]) => {
          const knob = byPath.get(path);
          if (!knob) return null;
          return (
            <KnobRow key={path} knob={knob} doc={doc} published={published} stage={stage}
                     fmt={ui.fmt} step={ui.step} min={ui.min} max={ui.max} />
          );
        })}
      </div>
    </div>
  );
}
