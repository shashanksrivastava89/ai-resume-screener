// Viewing the one scoring model as it applies in one jurisdiction (2026-09-12).
//
// There is ONE model for the whole organisation. This page presented it as
// "India (IN)" with a region selector that snapped back to India and toasted
// "this pilot wires the India model" — so a policy maker in Hungary or California
// had no way to see what the model does for their requisitions, and an approver
// was told a scoring-model change "applies to India (IN) requisitions only",
// which was never true.
//
// The lens is READ-ONLY by construction. Editing the model is a global act and
// belongs on the global view; changing what a signal does in one jurisdiction is
// a change to that jurisdiction's HIRING-RULES PACK, which lives on the
// Jurisdictions page. Projecting the effective values into the existing cards
// means every number on screen is the one a run would use there, with no second
// rendering path to drift.
import type { EffectiveModelView, ScoringDoc } from "../../types";

/** The published model with each signal's ENABLED state and cap replaced by what
 *  the jurisdiction actually uses.
 *
 *  A disabled signal keeps a positive cap on purpose: the live calculator posts
 *  this document to the real engine, whose schema requires `cap_points > 0`, and
 *  `enabled_default: false` is what stops it contributing. */
export function projectDoc(published: ScoringDoc, view: EffectiveModelView): ScoringDoc {
  const byId = new Map(view.signals.map((s) => [s.id, s]));
  return {
    ...published,
    signals: published.signals.map((signal) => {
      const row = byId.get(signal.id);
      if (!row) return signal;
      return {
        ...signal,
        enabled_default: row.enabled_here,
        cap_points: row.effective_cap ?? signal.cap_points,
      };
    }),
  };
}

/** The one-line chip beside a signal's name, or null when this jurisdiction has
 *  no opinion about it. Says which pack decided, because "off" without a "who"
 *  sends the reader looking in the wrong file. */
export function lensChip(row: EffectiveModelView["signals"][number],
                         pack: string | null): string | null {
  if (!row.enabled_here) {
    return row.disabled_reason ? capitalise(row.disabled_reason) : null;
  }
  if (row.cap_origin === "pack" && row.effective_cap !== null) {
    const direction = row.cap_raised ? "raised to" : "held to";
    return `${pack ?? "This location"}: ${direction} ±${row.effective_cap} (model ±${row.model_cap})`;
  }
  return null;
}

/** The market settings behind a signal, named with the pack that set each one —
 *  "short stint 9 mo (IN)". Params deep-merge, so a reader should be able to tell
 *  a regional value from an inherited default. */
export function paramLine(row: EffectiveModelView["signals"][number]): string | null {
  const entries = Object.entries(row.params ?? {});
  if (!entries.length) return null;
  const parts = entries
    .filter(([, value]) => !Array.isArray(value) || value.length > 0)
    .map(([key, value]) => {
      const origin = row.param_origins?.[key];
      return `${key.replace(/_/g, " ")} ${format(value)}${origin ? ` (${origin})` : ""}`;
    });
  return parts.length ? parts.join(" · ") : null;
}

function format(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
