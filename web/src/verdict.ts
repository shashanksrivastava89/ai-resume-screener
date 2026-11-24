// Who sees the AI's verdict — mirrors the server's rbac.may_view_verdict. Since
// 2026-10-21 every role that can open candidates holds `verdict_view` (HR-only from
// 2026-10-14); a deployment can still narrow it, and then the server takes the band,
// score, evidence, score math and the AI's ranking order out of every payload, so
// the UI renders what arrived — by PRESENCE, never an empty slot. The team's
// DECISIONS are a separate rule: payloads say `decisions_visible`.
//
// canSeeVerdict() is only for the few controls that have no data to go by before
// they are used (the Ask-Claude button: its whole content is the verdict).
import type { Me, ReviewCard } from "./types";

export function canSeeVerdict(me: Me | null): boolean {
  return (me?.capabilities ?? []).includes("verdict_view");
}

/** Did this review payload carry the verdict at all? Data-driven, so a list renders
 *  the same way whichever payload produced it. */
export function hasVerdict(cards: ReviewCard[]): boolean {
  return cards.some((c) => c.score != null);
}
