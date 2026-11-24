// P51: the ONE wording source for "what changed since this screen ran" — the
// Candidates preflight panel, the Review stale banner and the re-screen dialog
// all render drift through these helpers so they can never disagree.
import type { DriftItem } from "../types";

/** One drift item → plain lines: the headline, then each knob-level change with
 *  the publisher's name and reason where known. */
export function driftLines(item: DriftItem): string[] {
  const lines: string[] = [item.headline];
  for (const c of item.changes) {
    lines.push(`${c.label}: ${c.from} → ${c.to}`);
  }
  for (const p of item.publishes) {
    if (p.author || p.rationale) {
      lines.push(`— ${p.author ?? "someone"}${p.approved_by ? ` (approved by ${p.approved_by})` : ""}${p.rationale ? `: “${p.rationale}”` : ""}`);
    }
  }
  return lines;
}

export function allDriftLines(details: DriftItem[]): string[] {
  return details.flatMap(driftLines);
}
