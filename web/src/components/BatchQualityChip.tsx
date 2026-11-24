// The batch-level quality chip (2026-07-19): "Batch 78 · Strong" — the mean
// final score of the top gate-passing candidates vs the governed
// batch_quality thresholds (scoring model → Batch quality tab). A read on the
// BATCH, never on any single candidate — the tooltip says so explicitly.
import type { BatchQuality } from "../types";

const BATCH_CHIP: Record<string, { bg: string; fg: string }> = {
  strong: { bg: "var(--brand-green-tint)", fg: "var(--brand-green-deep)" },
  okay: { bg: "var(--surface-warn)", fg: "var(--warn-ink)" },
  weak: { bg: "var(--neutral-150)", fg: "var(--ink-soft)" },
};

export function batchTip(bq: BatchQuality): string {
  return `${bq.basis} — “Strong batch” ≥ ${bq.thresholds.strong_at}, “Okay” ≥ ${bq.thresholds.okay_at}. A batch read, not any candidate's score.`;
}

export default function BatchQualityChip({ bq, showScore = true }: { bq: BatchQuality; showScore?: boolean }) {
  const c = BATCH_CHIP[bq.band] ?? BATCH_CHIP.weak;
  const band = bq.label.replace(" batch", "");
  return (
    <span title={batchTip(bq)} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10.5, fontWeight: 700, padding: "2px 8px", borderRadius: 6, background: c.bg, color: c.fg, whiteSpace: "nowrap" }}>
      {/* the dashboard shows the band alone ("Batch: Strong"); working surfaces
          keep the number (the tooltip always carries the basis) */}
      {showScore ? `Batch ${bq.score != null ? bq.score : "—"} · ${band}` : `Batch: ${band}`}
    </span>
  );
}
